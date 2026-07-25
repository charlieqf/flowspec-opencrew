from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import StaticPool


REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_PATH = REPO_ROOT / "backend"
if str(BACKEND_PATH) not in sys.path:
    sys.path.insert(0, str(BACKEND_PATH))

from opcrew_backend.db.schema import (  # noqa: E402
    media_library_assets,
    media_library_clip_derivatives,
    media_library_storyboard_imports,
    metadata,
    openclip_tasks,
    session_files,
    sessions,
)
from opcrew_backend.media_library_clips import (  # noqa: E402
    ClipDerivativeRepository,
    ClipStorage,
    MediaClipError,
    MediaClipProcessor,
    build_ffmpeg_command,
    inspect_media_runtime,
    parse_ffprobe_payload,
    resolve_controlled_path,
    resolve_media_binary,
    safe_clip_filename,
)
from opcrew_backend.media_library_clips.models import (  # noqa: E402
    ClipRequest,
)


ASSET_ID = "asset-clip-storage-contract"
SOURCE_VERSION = "a" * 64
CLIP_ID = "mlc_0000000001000_aaaaaaaaaaaa"
IDEMPOTENCY_KEY = "clip-storage-key-0001"


class _SuccessfulPopen:
    def __init__(self, command: list[str], **_: object) -> None:
        self.command = command
        self.stdout = iter(
            ["out_time_us=100000\n", "out_time_us=250000\n"]
        )
        self.stderr = SimpleNamespace(read=lambda: "")
        self.returncode: int | None = None
        Path(command[-1]).write_bytes(b"fake-mp4-output")

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        self.returncode = 0
        return 0

    def terminate(self) -> None:
        self.returncode = -15

    def kill(self) -> None:
        self.returncode = -9


def _probe_completed(
    _command: list[str], **_: object
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=[],
        returncode=0,
        stdout=json.dumps(
            {
                "format": {"duration": "0.250"},
                "streams": [
                    {
                        "index": 0,
                        "codec_type": "video",
                        "codec_name": "h264",
                        "duration": "0.250",
                        "avg_frame_rate": "30/1",
                    }
                ],
            }
        ),
        stderr="",
    )


class _FailingRepository(ClipDerivativeRepository):
    def create_with_session_file(self, **_: object) -> tuple[dict, bool]:
        raise RuntimeError("forced_database_failure")


class MediaLibraryClipStorageContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        source = self.workspace / "inbox" / "源视频.mp4"
        source.parent.mkdir()
        source.write_bytes(b"immutable-source-video")
        self.source_relative = "inbox/源视频.mp4"
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        metadata.create_all(self.engine)
        with self.engine.begin() as conn:
            self.session_id = int(
                conn.execute(
                    sessions.insert()
                    .values(
                        source="open-cut-v1",
                        group_id="open-cut-v1",
                        title="clip storage contract",
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
                    display_name="源视频",
                    original_filename="源视频.mp4",
                    source_video_path=self.source_relative,
                    content_sha256=SOURCE_VERSION,
                    content_hashed_at=1,
                    media_type="video",
                    duration_ms=10_000,
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
        self.repository = ClipDerivativeRepository(self.engine)

    def tearDown(self) -> None:
        self.engine.dispose()
        self.temporary.cleanup()

    def request(
        self,
        *,
        idempotency_key: str = IDEMPOTENCY_KEY,
        start_ms: int = 0,
        end_ms: int = 250,
        display_name: str = "核心片段",
    ) -> ClipRequest:
        return ClipRequest(
            idempotency_key=idempotency_key,
            source_asset_id=ASSET_ID,
            source_session_id=self.session_id,
            source_version=SOURCE_VERSION,
            source_start_ms=start_ms,
            source_end_ms=end_ms,
            source_duration_ms=10_000,
            source_workspace=self.workspace,
            source_video_path=self.source_relative,
            display_name=display_name,
            manual_override=True,
        )

    def derivative_values(
        self,
        request: ClipRequest,
        *,
        clip_id: str = CLIP_ID,
        output_path: str | None = None,
        created_at: int = 1_000,
    ) -> dict[str, object]:
        return {
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
            "output_path": output_path
            or f"SessionOutput/clips/{clip_id}/核心片段.mp4",
            "display_name": request.display_name,
            "duration_ms": request.requested_duration_ms,
            "content_sha256": "b" * 64,
            "size_bytes": 123,
            "operation": request.operation,
            "search_eligible": False,
            "created_at": created_at,
        }

    def persist(
        self,
        request: ClipRequest | None = None,
        *,
        clip_id: str = CLIP_ID,
        created_at: int = 1_000,
    ) -> tuple[dict[str, object], Path]:
        request = request or self.request()
        storage = ClipStorage(self.workspace)
        paths = storage.allocate(clip_id, request.display_name)
        paths.final_path.write_bytes(b"registered-clip")
        values = self.derivative_values(
            request,
            clip_id=clip_id,
            output_path=paths.relative_path,
            created_at=created_at,
        )
        record, inserted = self.repository.create_with_session_file(
            values=values,
            request=request,
            updated_at=created_at,
        )
        self.assertTrue(inserted)
        return record, paths.final_path

    def test_binary_resolution_order_is_env_then_bundled_then_path(
        self,
    ) -> None:
        fake_repo = self.root / "repo"
        bundled = fake_repo / "ToolLibrary" / ".bin" / "ffmpeg"
        bundled.parent.mkdir(parents=True)
        bundled.write_text("#!/bin/sh\n", encoding="utf-8")
        bundled.chmod(bundled.stat().st_mode | stat.S_IXUSR)
        configured = self.root / "configured-ffmpeg"
        configured.write_text("#!/bin/sh\n", encoding="utf-8")
        configured.chmod(configured.stat().st_mode | stat.S_IXUSR)
        path_binary = self.root / "path-ffmpeg"
        path_binary.write_text("#!/bin/sh\n", encoding="utf-8")
        path_binary.chmod(path_binary.stat().st_mode | stat.S_IXUSR)

        self.assertEqual(
            resolve_media_binary(
                "ffmpeg",
                environ={"OPENCREW_FFMPEG_PATH": str(configured)},
                repo_root=fake_repo,
                which=lambda _name: str(path_binary),
            ),
            str(configured.resolve()),
        )
        self.assertEqual(
            resolve_media_binary(
                "ffmpeg",
                environ={},
                repo_root=fake_repo,
                which=lambda _name: str(path_binary),
            ),
            str(bundled.resolve()),
        )
        bundled.unlink()
        self.assertEqual(
            resolve_media_binary(
                "ffmpeg",
                environ={},
                repo_root=fake_repo,
                which=lambda _name: str(path_binary),
            ),
            str(path_binary.resolve()),
        )
        with self.assertRaises(MediaClipError) as raised:
            resolve_media_binary(
                "ffmpeg",
                environ={
                    "OPENCREW_FFMPEG_PATH": str(self.root / "missing")
                },
                repo_root=fake_repo,
                which=lambda _name: str(path_binary),
            )
        self.assertEqual(
            raised.exception.code, "media_binary_configuration_invalid"
        )

    def test_runtime_inspection_records_only_paths_versions_capabilities(
        self,
    ) -> None:
        def fake_run(
            command: list[str], **_: object
        ) -> subprocess.CompletedProcess[str]:
            if "-encoders" in command:
                stdout = (
                    "Encoders:\n"
                    " V....D libx264 H.264\n"
                    " A....D aac AAC\n"
                )
            elif "ffprobe" in command[0]:
                stdout = "ffprobe version 7.0\n"
            else:
                stdout = "ffmpeg version 7.0\n"
            return subprocess.CompletedProcess(
                command, 0, stdout=stdout, stderr=""
            )

        runtime = inspect_media_runtime(
            ffmpeg_path="/opt/opencrew/ffmpeg",
            ffprobe_path="/opt/opencrew/ffprobe",
            run=fake_run,
        )
        self.assertEqual(runtime["ffmpeg_version"], "ffmpeg version 7.0")
        self.assertEqual(runtime["ffprobe_version"], "ffprobe version 7.0")
        self.assertEqual(
            runtime["capabilities"], {"libx264": True, "aac": True}
        )
        self.assertEqual(
            set(runtime),
            {
                "ffmpeg_path",
                "ffprobe_path",
                "ffmpeg_version",
                "ffprobe_version",
                "capabilities",
            },
        )

    def test_ffmpeg_command_has_one_input_side_seek_and_optional_audio(
        self,
    ) -> None:
        command = build_ffmpeg_command(
            ffmpeg_path="/usr/local/bin/ffmpeg",
            source_path=self.workspace / self.source_relative,
            part_path=self.workspace / ".clip.part.mp4",
            start_ms=543_217,
            end_ms=543_467,
        )
        self.assertEqual(command.count("-ss"), 1)
        self.assertLess(command.index("-ss"), command.index("-i"))
        self.assertEqual(command[command.index("-ss") + 1], "543.217")
        self.assertEqual(command[command.index("-t") + 1], "0.250")
        self.assertEqual(
            command[command.index("-ss") + 2], "-accurate_seek"
        )
        self.assertNotIn("-c", command)
        self.assertIn("0:a?", command)
        self.assertEqual(command[-2], "-y")
        self.assertIsInstance(command, list)

    def test_probe_duration_uses_dynamic_audio_and_frame_tolerance(
        self,
    ) -> None:
        with_audio = parse_ffprobe_payload(
            {
                "format": {"duration": "0.250"},
                "streams": [
                    {
                        "codec_type": "video",
                        "codec_name": "h264",
                        "duration": "0.249",
                        "avg_frame_rate": "30/1",
                    },
                    {
                        "codec_type": "audio",
                        "codec_name": "aac",
                        "duration": "0.250",
                        "sample_rate": "48000",
                    },
                ],
            },
            requested_duration_ms=250,
            size_bytes=10,
        )
        self.assertTrue(with_audio.has_audio)
        self.assertEqual(with_audio.actual_duration_ms, 250)
        self.assertEqual(with_audio.video_frame_budget_ms, 34)
        self.assertEqual(with_audio.audio_frame_budget_ms, 22)
        self.assertEqual(with_audio.duration_tolerance_ms, 34)

        decimal_sample_rate = parse_ffprobe_payload(
            {
                "format": {"duration": "0.250"},
                "streams": [
                    {
                        "codec_type": "video",
                        "codec_name": "h264",
                        "duration": "0.250",
                        "avg_frame_rate": "30/1",
                    },
                    {
                        "codec_type": "audio",
                        "codec_name": "aac",
                        "duration": "0.250",
                        "sample_rate": "44100.0",
                    },
                ],
            },
            requested_duration_ms=250,
            size_bytes=10,
        )
        self.assertEqual(decimal_sample_rate.sample_rate, 44_100)

        without_audio = parse_ffprobe_payload(
            {
                "format": {"duration": "0.250"},
                "streams": [
                    {
                        "codec_type": "video",
                        "codec_name": "h264",
                        "duration": "0.250",
                        "avg_frame_rate": "24/1",
                    }
                ],
            },
            requested_duration_ms=250,
            size_bytes=10,
        )
        self.assertFalse(without_audio.has_audio)
        self.assertEqual(without_audio.video_frame_budget_ms, 42)
        self.assertEqual(without_audio.audio_frame_budget_ms, 0)
        self.assertEqual(without_audio.duration_tolerance_ms, 42)

        sixty_fps = parse_ffprobe_payload(
            {
                "format": {"duration": "0.267"},
                "streams": [
                    {
                        "codec_type": "video",
                        "codec_name": "h264",
                        "duration": "0.267",
                        "avg_frame_rate": "60/1",
                    }
                ],
            },
            requested_duration_ms=250,
            size_bytes=10,
        )
        self.assertEqual(sixty_fps.video_frame_budget_ms, 17)
        self.assertEqual(sixty_fps.duration_tolerance_ms, 17)

        invalid_rate_fallback = parse_ffprobe_payload(
            {
                "format": {"duration": "0.270"},
                "streams": [
                    {
                        "codec_type": "video",
                        "codec_name": "h264",
                        "duration": "0.270",
                        "avg_frame_rate": "0/0",
                    }
                ],
            },
            requested_duration_ms=250,
            size_bytes=10,
        )
        self.assertEqual(invalid_rate_fallback.video_frame_budget_ms, 50)
        self.assertEqual(invalid_rate_fallback.duration_tolerance_ms, 50)

        with self.assertRaises(MediaClipError) as raised:
            parse_ffprobe_payload(
                {
                    "format": {"duration": "0.300"},
                    "streams": [
                        {
                            "codec_type": "video",
                            "duration": "0.300",
                            "avg_frame_rate": "30/1",
                        }
                    ],
                },
                requested_duration_ms=250,
                size_bytes=10,
            )
        self.assertEqual(
            raised.exception.code, "media_clip_duration_mismatch"
        )

    def test_repository_atomically_registers_derivative_and_session_file(
        self,
    ) -> None:
        request = self.request()
        values = self.derivative_values(request)
        record, inserted = self.repository.create_with_session_file(
            values=values,
            request=request,
            updated_at=1_000,
        )
        self.assertTrue(inserted)
        self.assertEqual(record["clip_id"], CLIP_ID)
        with self.engine.connect() as conn:
            registered = conn.execute(
                select(session_files).where(
                    session_files.c.session_id == self.session_id,
                    session_files.c.path == values["output_path"],
                )
            ).mappings().one()
        self.assertEqual(registered["origin"], "media_library_clip")
        self.assertEqual(registered["downloadable"], 1)
        self.assertEqual(registered["size"], 123)

        replay, replay_inserted = (
            self.repository.create_with_session_file(
                values=values,
                request=request,
                updated_at=1_001,
            )
        )
        self.assertFalse(replay_inserted)
        self.assertEqual(replay["clip_id"], CLIP_ID)
        conflict = self.request(start_ms=250, end_ms=500)
        with self.assertRaises(MediaClipError) as raised:
            self.repository.create_with_session_file(
                values=self.derivative_values(conflict),
                request=conflict,
                updated_at=1_002,
            )
        self.assertEqual(raised.exception.code, "idempotency_key_conflict")

    def test_session_file_conflict_rolls_back_derivative_insert(self) -> None:
        request = self.request(
            idempotency_key="clip-storage-key-atomic-1"
        )
        values = self.derivative_values(request)
        with self.engine.begin() as conn:
            conn.execute(
                session_files.insert().values(
                    session_id=self.session_id,
                    path=values["output_path"],
                    kind="video",
                    size=1,
                    origin="preexisting",
                    downloadable=0,
                    stale=0,
                    updated_at=1,
                )
            )
        with self.assertRaises(IntegrityError):
            self.repository.create_with_session_file(
                values=values,
                request=request,
                updated_at=1_000,
            )
        self.assertIsNone(self.repository.get(CLIP_ID))
        self.assertIsNone(
            self.repository.get_by_idempotency_key(request.idempotency_key)
        )

    def test_processor_removes_final_file_when_database_publish_fails(
        self,
    ) -> None:
        repository = _FailingRepository(self.engine)
        processor = MediaClipProcessor(
            repository,
            ffmpeg_path="/usr/local/bin/ffmpeg",
            ffprobe_path="/usr/local/bin/ffprobe",
            popen=_SuccessfulPopen,
            run_command=_probe_completed,
        )
        with self.assertRaisesRegex(RuntimeError, "forced_database_failure"):
            processor.run(
                request=self.request(),
                clip_id=CLIP_ID,
                timestamp_ms=1_000,
                cancel_requested=lambda: False,
                on_progress=lambda _value: None,
                on_process=lambda _process: None,
                on_part_path=lambda _path: None,
            )
        output_root = self.workspace / "SessionOutput" / "clips"
        self.assertFalse(any(output_root.rglob("*.mp4")))
        with self.engine.connect() as conn:
            count = len(
                conn.execute(select(media_library_clip_derivatives)).fetchall()
            )
            file_count = len(
                conn.execute(
                    select(session_files).where(
                        session_files.c.origin == "media_library_clip"
                    )
                ).fetchall()
            )
        self.assertEqual(count, 0)
        self.assertEqual(file_count, 0)

    def test_processor_publishes_only_after_probe_and_registers_success(
        self,
    ) -> None:
        progress: list[int] = []
        processor = MediaClipProcessor(
            self.repository,
            ffmpeg_path="/usr/local/bin/ffmpeg",
            ffprobe_path="/usr/local/bin/ffprobe",
            popen=_SuccessfulPopen,
            run_command=_probe_completed,
        )
        record, inserted = processor.run(
            request=self.request(),
            clip_id=CLIP_ID,
            timestamp_ms=1_000,
            cancel_requested=lambda: False,
            on_progress=progress.append,
            on_process=lambda _process: None,
            on_part_path=lambda _path: None,
        )
        self.assertTrue(inserted)
        self.assertEqual(record["duration_ms"], 250)
        final_path = resolve_controlled_path(
            self.workspace,
            str(record["output_path"]),
            must_exist=True,
        )
        self.assertEqual(final_path.read_bytes(), b"fake-mp4-output")
        self.assertEqual(
            record["content_sha256"],
            ClipStorage.sha256_file(final_path),
        )
        self.assertEqual(progress[-1], 99)
        self.assertFalse(
            any(final_path.parent.glob("*.part.mp4")),
            "successful publish left a part file",
        )
        self.assertTrue(
            self.repository.session_file_registered(
                self.session_id, str(record["output_path"])
            )
        )

    def test_list_and_get_are_asset_scoped_and_stably_ordered(self) -> None:
        first_request = self.request(
            idempotency_key="clip-storage-key-0002",
            display_name="first",
        )
        second_request = self.request(
            idempotency_key="clip-storage-key-0003",
            start_ms=250,
            end_ms=500,
            display_name="second",
        )
        first, _ = self.persist(
            first_request,
            clip_id="mlc_0000000001001_bbbbbbbbbbbb",
            created_at=1_000,
        )
        second, _ = self.persist(
            second_request,
            clip_id="mlc_0000000001002_cccccccccccc",
            created_at=2_000,
        )
        listed = self.repository.list_for_asset(ASSET_ID)
        self.assertEqual(
            [row["clip_id"] for row in listed],
            [second["clip_id"], first["clip_id"]],
        )
        self.assertEqual(
            self.repository.get_for_asset(
                ASSET_ID, str(first["clip_id"])
            )["clip_id"],
            first["clip_id"],
        )
        self.assertIsNone(
            self.repository.get_for_asset(
                "another-asset", str(first["clip_id"])
            )
        )

    def test_storage_paths_are_workspace_bounded_unicode_safe_and_no_symlinks(
        self,
    ) -> None:
        self.assertEqual(
            safe_clip_filename("../../产品 核心?.mp4"), "产品_核心.mp4"
        )
        storage = ClipStorage(self.workspace)
        paths = storage.allocate(CLIP_ID, "产品核心")
        self.assertTrue(
            paths.final_path.is_relative_to(self.workspace.resolve())
        )
        self.assertEqual(
            paths.relative_path,
            f"SessionOutput/clips/{CLIP_ID}/产品核心.mp4",
        )
        self.assertTrue(paths.part_path.name.startswith(".产品核心.mp4."))
        self.assertTrue(paths.part_path.name.endswith(".part.mp4"))
        with self.assertRaises(MediaClipError):
            resolve_controlled_path(self.workspace, "../escape.mp4")
        with self.assertRaises(MediaClipError):
            resolve_controlled_path(self.workspace, "/tmp/escape.mp4")

        outside = self.root / "outside.mp4"
        outside.write_bytes(b"outside")
        symlink = self.workspace / "inbox" / "escape.mp4"
        symlink.symlink_to(outside)
        with self.assertRaises(MediaClipError):
            storage.source_file("inbox/escape.mp4")

        inside = self.workspace / "inbox" / "inside.mp4"
        inside.write_bytes(b"inside")
        inside_link = self.workspace / "inbox" / "inside-link.mp4"
        inside_link.symlink_to(inside)
        with self.assertRaises(MediaClipError):
            storage.source_file("inbox/inside-link.mp4")

        actual_directory = self.workspace / "inbox" / "actual-directory"
        actual_directory.mkdir()
        (actual_directory / "source.mp4").write_bytes(b"inside-parent")
        parent_link = self.workspace / "inbox" / "linked-directory"
        parent_link.symlink_to(actual_directory, target_is_directory=True)
        with self.assertRaises(MediaClipError):
            storage.source_file("inbox/linked-directory/source.mp4")

        final_link_id = "mlc_0000000001001_bbbbbbbbbbbb"
        final_link_dir = (
            self.workspace
            / "SessionOutput"
            / "clips"
            / final_link_id
        )
        final_link_dir.mkdir(parents=True)
        (final_link_dir / "final-link.mp4").symlink_to(inside)
        with self.assertRaises(MediaClipError):
            storage.allocate(final_link_id, "final-link")

    def test_orphan_cleanup_preserves_registered_and_new_files(self) -> None:
        registered_request = self.request()
        record, registered_file = self.persist(registered_request)
        orphan_id = "mlc_0000000001001_bbbbbbbbbbbb"
        orphan_dir = (
            self.workspace / "SessionOutput" / "clips" / orphan_id
        )
        orphan_dir.mkdir(parents=True)
        old_part = orphan_dir / ".orphan.mp4.token.part.mp4"
        old_final = orphan_dir / "orphan.mp4"
        recent_part = orphan_dir / ".recent.mp4.token.part.mp4"
        for path in (old_part, old_final, recent_part):
            path.write_bytes(b"x")
        old_seconds = (int(time.time() * 1000) - 100_000) / 1000
        os.utime(old_part, (old_seconds, old_seconds))
        os.utime(old_final, (old_seconds, old_seconds))

        report = ClipStorage(self.workspace).cleanup_orphans(
            registered_output_paths={str(record["output_path"])},
            now_ms=int(time.time() * 1000),
            ttl_ms=10_000,
        )
        self.assertTrue(registered_file.is_file())
        self.assertFalse(old_part.exists())
        self.assertFalse(old_final.exists())
        self.assertTrue(recent_part.exists())
        self.assertEqual(len(report.removed_parts), 1)
        self.assertEqual(len(report.removed_orphans), 1)
        self.assertEqual(report.missing_registered, ())

    def test_delete_refuses_storyboard_reference_then_removes_file_and_rows(
        self,
    ) -> None:
        record, final_path = self.persist()
        target_workspace = self.root / "target"
        target_workspace.mkdir()
        with self.engine.begin() as conn:
            target_session_id = int(
                conn.execute(
                    sessions.insert()
                    .values(
                        source="openclip",
                        group_id="openclip",
                        title="target",
                        status="draft",
                        workspace_dir=str(target_workspace),
                        created_at=2,
                        updated_at=2,
                    )
                    .returning(sessions.c.id)
                ).scalar_one()
            )
            target_task_id = int(
                conn.execute(
                    openclip_tasks.insert()
                    .values(
                        session_id=target_session_id,
                        status="draft",
                        created_at=2,
                        updated_at=2,
                    )
                    .returning(openclip_tasks.c.id)
                ).scalar_one()
            )
            conn.execute(
                media_library_storyboard_imports.insert().values(
                    import_id="mli_clip_storage_reference",
                    idempotency_key="clip-import-key-0001",
                    source_kind="media_library_clip",
                    source_asset_id=ASSET_ID,
                    source_clip_id=CLIP_ID,
                    source_version=SOURCE_VERSION,
                    target_task_id=target_task_id,
                    target_session_id=target_session_id,
                    target_path="SessionOutput/storyboard/assets/videos/a.mp4",
                    target_manifest_asset_id="asset_manifest_1",
                    content_sha256="b" * 64,
                    size_bytes=123,
                    status="completed",
                    created_at=2,
                    updated_at=2,
                )
            )
        self.assertTrue(self.repository.is_in_use(CLIP_ID))
        with self.assertRaises(MediaClipError) as raised:
            self.repository.delete_after_file_removal(
                asset_id=ASSET_ID, clip_id=CLIP_ID
            )
        self.assertEqual(raised.exception.code, "media_clip_in_use")
        self.assertTrue(final_path.is_file())

        with self.engine.begin() as conn:
            conn.execute(media_library_storyboard_imports.delete())
        ClipStorage(self.workspace).delete_file_transactionally(
            relative_path=str(record["output_path"]),
            delete_database=lambda: (
                self.repository.delete_after_file_removal(
                    asset_id=ASSET_ID, clip_id=CLIP_ID
                )
            ),
        )
        self.assertFalse(final_path.exists())
        self.assertIsNone(self.repository.get(CLIP_ID))
        with self.engine.connect() as conn:
            registered = conn.execute(
                select(session_files).where(
                    session_files.c.session_id == self.session_id,
                    session_files.c.path == record["output_path"],
                )
            ).first()
        self.assertIsNone(registered)


if __name__ == "__main__":
    unittest.main()
