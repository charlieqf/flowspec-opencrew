from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any, Mapping
from unittest.mock import patch

from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import IntegrityError


REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_PATH = REPO_ROOT / "backend"
if str(BACKEND_PATH) not in sys.path:
    sys.path.insert(0, str(BACKEND_PATH))

from opcrew_backend.db.schema import (  # noqa: E402
    media_library_assets,
    media_library_clip_derivatives,
    metadata,
    session_files,
    sessions,
)
from opcrew_backend.media_library_clips import (  # noqa: E402
    ClipDerivativeRepository,
    ClipJobManager,
    duration_tolerance_ms,
    inspect_media_runtime,
    resolve_media_binary,
)
from opcrew_backend.media_library_clips.models import (  # noqa: E402
    ClipRequest,
)


ASSET_ID = "asset-real-ffmpeg-db-publish-failure"
BOOT_ID = "7" * 32
IDEMPOTENCY_KEY = "real-ffmpeg-db-failure-0001"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class _PublishObservingRepository(ClipDerivativeRepository):
    """Observe the real publish boundary; the database trigger causes failure."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.publish_attempts = 0
        self.integrity_errors = 0
        self.observed_values: dict[str, Any] | None = None
        self.final_existed_at_publish = False
        self.part_files_at_publish: tuple[str, ...] = ()
        self.final_sha256_at_publish: str | None = None

    def create_with_session_file(
        self,
        *,
        values: Mapping[str, Any],
        request: ClipRequest,
        updated_at: int,
    ) -> tuple[dict[str, Any], bool]:
        self.publish_attempts += 1
        self.observed_values = dict(values)
        final_path = (
            request.source_workspace / str(values["output_path"])
        ).resolve()
        self.final_existed_at_publish = final_path.is_file()
        self.part_files_at_publish = tuple(
            path.name for path in final_path.parent.glob("*.part.mp4")
        )
        if self.final_existed_at_publish:
            self.final_sha256_at_publish = _sha256(final_path)
        try:
            return super().create_with_session_file(
                values=values,
                request=request,
                updated_at=updated_at,
            )
        except IntegrityError:
            self.integrity_errors += 1
            raise


class MediaLibraryRealFfmpegDbPublishFailureTest(unittest.TestCase):
    """Run repository FFmpeg 7.0, then fail the actual DB transaction."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.workspace = self.root / "含中文 workspace"
        self.workspace.mkdir()
        self.source = self.workspace / "inbox" / "真实源视频.mp4"
        self.source.parent.mkdir()

        self.ffmpeg_path = resolve_media_binary(
            "ffmpeg",
            environ={},
            repo_root=REPO_ROOT,
            which=lambda _name: None,
        )
        self.ffprobe_path = resolve_media_binary(
            "ffprobe",
            environ={},
            repo_root=REPO_ROOT,
            which=lambda _name: None,
        )
        self.runtime = inspect_media_runtime(
            ffmpeg_path=self.ffmpeg_path,
            ffprobe_path=self.ffprobe_path,
        )
        self.assertEqual(
            Path(self.ffmpeg_path),
            (REPO_ROOT / "ToolLibrary" / ".bin" / "ffmpeg").resolve(),
        )
        self.assertEqual(
            Path(self.ffprobe_path),
            (REPO_ROOT / "ToolLibrary" / ".bin" / "ffprobe").resolve(),
        )
        self.assertTrue(
            self.runtime["ffmpeg_version"].startswith("ffmpeg version 7.0")
        )

        fixture_command = [
            self.ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x180:rate=30:duration=1",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=880:sample_rate=48000:duration=1",
            "-shortest",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            "-y",
            str(self.source),
        ]
        subprocess.run(
            fixture_command,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        self.source_sha256 = _sha256(self.source)
        self.source_stat = self.source.stat()

        database_path = self.root / "acceptance.sqlite3"
        self.engine = create_engine(
            f"sqlite+pysqlite:///{database_path}",
            connect_args={"check_same_thread": False},
        )
        metadata.create_all(self.engine)
        with self.engine.begin() as conn:
            self.session_id = int(
                conn.execute(
                    sessions.insert()
                    .values(
                        source="open-cut-v1",
                        group_id="open-cut-v1",
                        title="real ffmpeg DB failure acceptance",
                        status="draft",
                        workspace_dir=str(self.workspace),
                        created_at=1,
                        updated_at=1,
                    )
                    .returning(sessions.c.id)
                ).scalar_one()
            )
            conn.execute(
                media_library_assets.insert().values(
                    asset_id=ASSET_ID,
                    session_id=self.session_id,
                    display_name="真实源视频",
                    original_filename=self.source.name,
                    source_video_path="inbox/真实源视频.mp4",
                    content_sha256=self.source_sha256,
                    content_hashed_at=1,
                    media_type="video",
                    duration_ms=1_000,
                    upload_status="ready",
                    analysis_status="not_analyzed",
                    subtitle_mode="unknown",
                    analysis_summary_json={},
                    tags_json=[],
                    archived=False,
                    referenced_by_count=0,
                    created_at=1,
                    updated_at=1,
                )
            )
            conn.exec_driver_sql(
                """
                CREATE TRIGGER force_clip_derivative_publish_failure
                BEFORE INSERT ON media_library_clip_derivatives
                BEGIN
                    SELECT RAISE(ABORT, 'forced_db_publish_failure');
                END
                """
            )
        self.repository = _PublishObservingRepository(self.engine)
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.metrics: list[tuple[str, int]] = []
        with patch.dict(
            os.environ,
            {
                "OPENCREW_FFMPEG_PATH": self.ffmpeg_path,
                "OPENCREW_FFPROBE_PATH": self.ffprobe_path,
            },
        ):
            self.manager = ClipJobManager(
                self.engine,
                repository=self.repository,
                max_workers=1,
                boot_id=BOOT_ID,
                event_sink=lambda kind, payload: self.events.append(
                    (kind, dict(payload))
                ),
                metric_sink=lambda name, value: self.metrics.append(
                    (name, int(value))
                ),
            )

    def tearDown(self) -> None:
        self.manager.shutdown()
        self.engine.dispose()
        self.temporary.cleanup()

    def test_real_ffmpeg_db_publish_failure_cleans_every_output(self) -> None:
        job = self.manager.submit(
            asset={
                "asset_id": ASSET_ID,
                "session_id": self.session_id,
                "source_video_path": "inbox/真实源视频.mp4",
                "content_sha256": self.source_sha256,
                "duration_ms": 1_000,
                "upload_status": "ready",
                "archived": False,
            },
            session={
                "id": self.session_id,
                "workspace_dir": str(self.workspace),
            },
            payload={
                "source_version": self.source_sha256,
                "start_ms": 137,
                "end_ms": 387,
                "display_name": "数据库失败后必须清理",
                "manual_override": True,
                "idempotency_key": IDEMPOTENCY_KEY,
            },
        )
        deadline = time.monotonic() + 30
        terminal: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            terminal = self.manager.get_job(ASSET_ID, job["clip_job_id"])
            if terminal["status"] in {"completed", "failed", "cancelled"}:
                break
            time.sleep(0.01)
        self.assertIsNotNone(terminal)
        assert terminal is not None
        self.assertEqual(terminal["status"], "failed")
        self.assertEqual(
            terminal["error"],
            {
                "code": "media_clip_execution_failed",
                "user_message": "视频剪切失败，请稍后重试。",
            },
        )
        self.assertNotIn("forced_db_publish_failure", str(terminal))

        self.assertEqual(self.repository.publish_attempts, 1)
        self.assertEqual(self.repository.integrity_errors, 1)
        self.assertTrue(self.repository.final_existed_at_publish)
        self.assertEqual(self.repository.part_files_at_publish, ())
        observed = self.repository.observed_values
        self.assertIsNotNone(observed)
        assert observed is not None
        self.assertGreater(int(observed["size_bytes"]), 0)
        self.assertEqual(
            self.repository.final_sha256_at_publish,
            observed["content_sha256"],
        )
        dynamic_tolerance_ms, video_budget_ms, audio_budget_ms = (
            duration_tolerance_ms(
                requested_duration_ms=250,
                avg_frame_rate="30/1",
                audio_codec="aac",
                sample_rate=48_000,
            )
        )
        self.assertEqual(
            (dynamic_tolerance_ms, video_budget_ms, audio_budget_ms),
            (34, 34, 22),
        )
        self.assertLessEqual(
            abs(int(observed["duration_ms"]) - 250),
            dynamic_tolerance_ms,
            "30fps + AAC 250ms output must use dynamic frame tolerance",
        )

        output_root = self.workspace / "SessionOutput" / "clips"
        remaining_files = (
            tuple(path for path in output_root.rglob("*") if path.is_file())
            if output_root.exists()
            else ()
        )
        remaining_clip_directories = (
            tuple(path for path in output_root.iterdir() if path.is_dir())
            if output_root.exists()
            else ()
        )
        self.assertEqual(remaining_files, ())
        self.assertEqual(remaining_clip_directories, ())
        self.assertFalse(any(self.workspace.rglob("*.part.mp4")))
        self.assertFalse(any(self.workspace.rglob("*.deleting")))
        self.assertFalse(any(output_root.rglob("*.json")))

        with self.engine.connect() as conn:
            derivative_count = int(
                conn.execute(
                    select(func.count()).select_from(
                        media_library_clip_derivatives
                    )
                ).scalar_one()
            )
            registered_file_count = int(
                conn.execute(
                    select(func.count())
                    .select_from(session_files)
                    .where(session_files.c.origin == "media_library_clip")
                ).scalar_one()
            )
        self.assertEqual(derivative_count, 0)
        self.assertEqual(registered_file_count, 0)

        self.assertTrue(self.source.is_file())
        self.assertEqual(_sha256(self.source), self.source_sha256)
        source_after = self.source.stat()
        self.assertEqual(source_after.st_size, self.source_stat.st_size)
        self.assertEqual(source_after.st_mtime_ns, self.source_stat.st_mtime_ns)

        event_by_kind = {kind: payload for kind, payload in self.events}
        self.assertIn("media_library.clip.runtime", event_by_kind)
        self.assertIn("media_library.clip.requested", event_by_kind)
        self.assertIn("media_library.clip.failed", event_by_kind)
        self.assertEqual(
            event_by_kind["media_library.clip.failed"]["error"],
            terminal["error"],
        )
        self.assertNotIn(
            "forced_db_publish_failure",
            str(event_by_kind["media_library.clip.failed"]),
        )
        self.assertIn(
            (
                'media_library_clip_failure_total'
                '{code="media_clip_execution_failed"}',
                1,
            ),
            self.metrics,
        )
        self.assertEqual(self.metrics[-1], ("media_library_clip_active", 0))


if __name__ == "__main__":
    unittest.main()
