from __future__ import annotations

import importlib.util
import http.client
import json
import math
import os
import shutil
import subprocess
import struct
import sys
import tempfile
import unittest
import wave
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = REPO_ROOT / "backend"
for path in (REPO_ROOT, BACKEND_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
SCRIPT_PATH = REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "03_03_TTSBuilderQuickAdv.py"
TTS_BUILDER_G_PATH = REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "03_01_TTSBuilderG.py"
TTS_BUILDER_QUICK_PATH = REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "03_02_TTSBuilderQuick.py"
SCORING_PATH = REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "tts_quick_adv" / "scoring.py"
CORE_PATH = REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "tts_quick_adv" / "core.py"
CLI_PATH = REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "tts_quick_adv" / "cli.py"
CLONE_PROVIDER_PATH = REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "tts_quick_adv" / "providers" / "aliyun_voice_clone.py"
VOICE_CLONING_PATH = REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "tts_quick_adv" / "voice_cloning.py"
OPENCLIP_ROUTER_PATH = REPO_ROOT / "backend" / "opcrew_backend" / "koubo" / "router.py"
OPENCLIP_SCHEMAS_PATH = REPO_ROOT / "backend" / "opcrew_backend" / "koubo" / "schemas.py"
ANALYSIS_V1_TTS_BUILDER_COMPONENT_PATH = REPO_ROOT / "frontend" / "src" / "modules" / "koubo" / "AnalysisV1" / "components" / "AnalysisV1TTSBuilder.jsx"
TALKING_HEAD_CREATE_MODAL_PATH = REPO_ROOT / "frontend" / "src" / "modules" / "koubo" / "KouboTaskList" / "KouboTaskCreateFromScriptModal.jsx"
TOOLLIB_ROOT = REPO_ROOT / "ToolLibrary"
DEFAULT_MODEL = "gemini-3.1-flash-tts-preview"
QWEN_MODEL = "qwen3-tts-flash"
BYTEDANCE_MODEL = "seed-tts-1.1"


def load_scoring_module():
    spec = importlib.util.spec_from_file_location("analysis_v1_tts_quick_adv_scoring_contract", SCORING_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_core_module():
    from ToolLibrary.Analysis_V1.tts_quick_adv import core

    return core


def load_voice_cloning_module():
    from ToolLibrary.Analysis_V1.tts_quick_adv import voice_cloning

    return voice_cloning


def load_tts_builder_g_module():
    spec = importlib.util.spec_from_file_location("analysis_v1_tts_builder_g_contract", TTS_BUILDER_G_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_tts_builder_quick_module():
    spec = importlib.util.spec_from_file_location("analysis_v1_tts_builder_quick_contract", TTS_BUILDER_QUICK_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def ffmpeg_available() -> bool:
    candidates = [
        REPO_ROOT / ".bin" / "ffmpeg",
        REPO_ROOT / "vendor" / "static_ffmpeg" / "darwin_arm64" / "ffmpeg",
        TOOLLIB_ROOT / ".bin" / "ffmpeg",
        TOOLLIB_ROOT / "vendor" / "static_ffmpeg" / "darwin_arm64" / "ffmpeg",
    ]
    return any(path.exists() for path in candidates) or bool(shutil.which("ffmpeg"))


def ffprobe_available() -> bool:
    candidates = [
        REPO_ROOT / ".bin" / "ffprobe",
        REPO_ROOT / "vendor" / "static_ffmpeg" / "darwin_arm64" / "ffprobe",
        TOOLLIB_ROOT / ".bin" / "ffprobe",
        TOOLLIB_ROOT / "vendor" / "static_ffmpeg" / "darwin_arm64" / "ffprobe",
    ]
    return any(path.exists() for path in candidates) or bool(shutil.which("ffprobe"))


def write_tone_wav(path: Path, frequency: float, duration: float = 4.0, rate: int = 16000, amplitude: float = 0.28) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = bytearray()
    total = int(rate * duration)
    for index in range(total):
        envelope = min(1.0, index / max(1, int(rate * 0.08)), (total - index) / max(1, int(rate * 0.08)))
        sample = int(32767 * amplitude * envelope * math.sin(2.0 * math.pi * frequency * index / rate))
        frames.extend(struct.pack("<h", sample))
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(bytes(frames))


def write_qwen_placeholder_wav(path: Path, duration: float = 1.0, rate: int = 24000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = b"\0\0" * int(rate * duration)
    path.write_bytes(
        b"RIFF"
        + struct.pack("<I", 0x7FFFFFBF)
        + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
        + b"data"
        + struct.pack("<I", 0x7FFFFF9B)
        + frames
    )


def write_smoke_workspace(workspace: Path) -> None:
    (workspace / "SessionContext").mkdir(parents=True, exist_ok=True)
    (workspace / "SessionOutput" / "subtitle").mkdir(parents=True, exist_ok=True)
    (workspace / "SessionContext" / "Variables.json").write_text(json.dumps({
        "project": "tts quick adv smoke",
        "language": "zh",
    }, ensure_ascii=False), encoding="utf-8")
    (workspace / "SessionOutput" / "subtitle" / "final_srt_frame_items.json").write_text(json.dumps({
        "items": [
            {"start": 0.0, "end": 1.3, "dialogue": "今天我们来测试一个自然清楚的中文口播声音。"},
            {"start": 1.3, "end": 2.8, "dialogue": "它需要接近日常聊天，不要像广告或者播音。"},
            {"start": 2.8, "end": 4.0, "dialogue": "同时要保持稳定的音色和舒服的语速。"},
        ],
    }, ensure_ascii=False), encoding="utf-8")
    write_tone_wav(workspace / "SessionOutput" / "Audio_Reference.wav", 220.0, duration=4.5)


def write_smoke_catalog(catalog_dir: Path, *, provider: str = "google", model: str = DEFAULT_MODEL) -> None:
    if provider == "google":
        voices = [
            ("Aoede", "Aoede - smoke female", "female", 225.0),
            ("Achird", "Achird - smoke male", "male", 132.0),
            ("Algenib", "Algenib - smoke male", "male", 150.0),
        ]
    elif provider == "bytedance":
        voices = [
            ("zh_female_tianmeitaozi_mars_bigtts", "Sweet Peach - smoke female", "female", 225.0),
            ("zh_male_M392_conversation_wvae_bigtts", "M392 - smoke male", "male", 132.0),
            ("zh_male_qingshuangnanda_mars_bigtts", "Clear Male - smoke male", "male", 150.0),
        ]
    else:
        voices = [
            ("Cherry", "Cherry - smoke female", "female", 225.0),
            ("Ethan", "Ethan - smoke male", "male", 132.0),
            ("Moon", "Moon - smoke male", "male", 150.0),
        ]
    catalog_dir.mkdir(parents=True, exist_ok=True)
    index_voices = []
    for voice, label, gender, frequency in voices:
        filename = f"{voice}_fixed_cn_v1_16s.wav"
        write_tone_wav(catalog_dir / filename, frequency, duration=2.0)
        index_voices.append({
            "voice": voice,
            "voice_id": voice,
            "voice_label": label,
            "provider": provider,
            "model": model,
            "voice_mode": "preset",
            "language": "zh",
            "gender": gender,
            "style": "smoke",
            "sample_text_id": "fixed_cn_v1",
            "sample_audio_path": filename,
            "raw_duration": 2.0,
            "audio": {
                "path": filename,
                "duration": 2.0,
                "sample_rate": 16000,
                "channels": 1,
                "format": "wav",
            },
        })
    (catalog_dir / "voice_catalog_index.json").write_text(json.dumps({
        "schema_version": "analysis_v1_voice_catalog_index_smoke",
        "catalog_id": f"{provider}_smoke_fixed_cn_v1",
        "provider": provider,
        "model": model,
        "sample_text_id": "fixed_cn_v1",
        "sample_text": "你好，这是一段用于自动化测试的声音样本。",
        "count": len(index_voices),
        "voices": index_voices,
    }, ensure_ascii=False), encoding="utf-8")


def run_quick_adv_cli(workspace: Path, command: str, catalog_dir: Path | None = None, *extra: str) -> subprocess.CompletedProcess[str]:
    args = [
        sys.executable,
        str(SCRIPT_PATH),
        command,
        "--workspace",
        str(workspace),
        "--print-json",
    ]
    if catalog_dir is not None:
        args.extend(["--voice-catalog-dir", str(catalog_dir)])
    args.extend(extra)
    env = os.environ.copy()
    for key in ("OPENCREW_TTS_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY", "DASHSCOPE_API_KEY"):
        env.pop(key, None)
    env["ANALYSIS_V1_ENABLE_SPEECHBRAIN"] = "0"
    env["ANALYSIS_V1_ALLOW_SPEECHBRAIN_DOWNLOAD"] = "0"
    return subprocess.run(
        args,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
        env=env,
    )


class AnalysisV1TTSQuickAdvContractTest(unittest.TestCase):
    def test_script_exists_and_exposes_state_subcommand(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            completed = subprocess.run(
                [sys.executable, str(SCRIPT_PATH), "state", "--workspace", tmp, "--print-json"],
                cwd=str(REPO_ROOT),
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)
        payload = json.loads(completed.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["tool"], "03_03_TTSBuilderQuickAdv")
        self.assertEqual(payload["cloned_voices"], [])

    def test_rank_blocks_with_stable_missing_input_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            completed = subprocess.run(
                [sys.executable, str(SCRIPT_PATH), "rank", "--workspace", tmp, "--print-json"],
                cwd=str(REPO_ROOT),
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)
        payload = json.loads(completed.stdout)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["status"], "blocked")
        self.assertEqual(payload["blocked_reasons"][0]["code"], "required_input_missing")

    def test_run_failed_returns_nonzero_exit_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / "SessionContext").mkdir(parents=True)
            (workspace / "SessionOutput" / "subtitle").mkdir(parents=True)
            (workspace / "SessionOutput").mkdir(parents=True, exist_ok=True)
            (workspace / "SessionContext" / "Variables.json").write_text("{bad json", encoding="utf-8")
            (workspace / "SessionOutput" / "subtitle" / "final_srt_frame_items.json").write_text(json.dumps({"items": [{"text": "测试", "start": 0, "end": 1}]}), encoding="utf-8")
            (workspace / "SessionOutput" / "Audio_Reference.wav").write_bytes(b"not-a-real-wav")
            completed = subprocess.run(
                [sys.executable, str(SCRIPT_PATH), "run", "--workspace", tmp, "--print-json"],
                cwd=str(REPO_ROOT),
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )

        self.assertEqual(completed.returncode, 1, completed.stderr or completed.stdout)
        payload = json.loads(completed.stdout)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["status"], "failed")

    def test_clone_list_blocks_with_stable_missing_key_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            completed = subprocess.run(
                [sys.executable, str(SCRIPT_PATH), "clone-list", "--workspace", tmp, "--clone-api-key-env", "OPENCREW_TEST_MISSING_CLONE_KEY", "--print-json"],
                cwd=str(REPO_ROOT),
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)
        payload = json.loads(completed.stdout)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["status"], "blocked")
        self.assertEqual(payload["blocked_reasons"][0]["code"], "clone_api_key_missing")

    def test_clone_voice_requires_explicit_consent_before_provider_call(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            completed = subprocess.run(
                [sys.executable, str(SCRIPT_PATH), "clone-voice", "--workspace", tmp, "--clone-api-key", "fake-key", "--clone-audio-url", "oss://fake/audio.wav", "--print-json"],
                cwd=str(REPO_ROOT),
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)
        payload = json.loads(completed.stdout)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["status"], "blocked")
        self.assertEqual(payload["blocked_reasons"][0]["code"], "clone_consent_required")

    def test_cloud_clone_record_is_promoted_to_tts_candidate(self) -> None:
        voice_cloning = load_voice_cloning_module()

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            record = {
                "provider": "aliyun_dashscope",
                "target_model": "cosyvoice-v3.5-flash",
                "voice_id": "ocadv_test_voice",
                "voice": "ocadv_test_voice",
                "voice_source": "cloud_clone",
                "reference_audio_sha256": "abc123",
                "created_at": "2026-06-16T00:00:00Z",
            }

            candidate = voice_cloning.upsert_clone_candidate(workspace, record)
            payload = json.loads((workspace / "SessionOutput" / "tts" / "tts_builder_candidates.json").read_text(encoding="utf-8"))

        self.assertEqual(candidate["provider"], "cosyvoice")
        self.assertEqual(candidate["model"], "cosyvoice-v3.5-flash")
        self.assertEqual(candidate["voice_source"], "cloud_clone")
        self.assertEqual(candidate["voice_id"], "ocadv_test_voice")
        self.assertEqual(payload["selected_candidate_id"], candidate["candidate_id"])
        self.assertEqual(payload["candidates"][0]["candidate_id"], candidate["candidate_id"])
        self.assertEqual(payload["candidates"][0]["score"], 100)

    def test_heygen_clone_record_is_promoted_to_tts_candidate(self) -> None:
        voice_cloning = load_voice_cloning_module()

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            record = {
                "provider": "heygen",
                "target_model": "heygen-voice-clone-v3",
                "voice_id": "heygen_voice_clone_001",
                "voice": "heygen_voice_clone_001",
                "voice_source": "cloud_clone",
                "reference_audio_sha256": "def456",
                "created_at": "2026-06-18T00:00:00Z",
            }

            candidate = voice_cloning.upsert_clone_candidate(workspace, record)

        self.assertEqual(candidate["provider"], "heygen")
        self.assertEqual(candidate["model"], "heygen-voice-clone-v3")
        self.assertEqual(candidate["voice_id"], "heygen_voice_clone_001")
        self.assertEqual(candidate["source_clone_provider"], "heygen")

    def test_heygen_cloud_voice_can_be_imported_to_current_task_candidates(self) -> None:
        voice_cloning = load_voice_cloning_module()
        old_query = voice_cloning.heygen_voice_clone.query_voice

        def fake_query(**kwargs: object) -> dict[str, object]:
            return {
                "provider": "heygen",
                "target_model": "heygen-voice-clone-v3",
                "voice": {
                    "voice_id": kwargs.get("voice_id"),
                    "voice_name": "ocadv_cloud_voice",
                    "language": "Chinese",
                    "gender": "male",
                },
            }

        voice_cloning.heygen_voice_clone.query_voice = fake_query
        try:
            with tempfile.TemporaryDirectory() as tmp:
                workspace = Path(tmp)
                args = voice_cloning.CloneVoiceArgs(
                    provider="heygen",
                    api_key="heygen-key",
                    api_key_env="",
                    workspace_id="",
                    enrollment_model="voice-enrollment",
                    target_model="heygen-voice-clone-v3",
                    prefix="ocadv",
                    audio_path="",
                    audio_url="",
                    language_hints="zh",
                    max_prompt_audio_length=16.0,
                    page_index=0,
                    page_size=100,
                    voice_id="heygen_cloud_voice_001",
                    consent_confirmed=False,
                    consent_actor="contract",
                    consent_note="",
                    force=False,
                )

                payload = voice_cloning.import_cloned_voice(workspace, args)
                records = json.loads((workspace / "SessionOutput" / "tts" / "cloud_voice_clones.json").read_text(encoding="utf-8"))
                candidates = json.loads((workspace / "SessionOutput" / "tts" / "tts_builder_candidates.json").read_text(encoding="utf-8"))
        finally:
            voice_cloning.heygen_voice_clone.query_voice = old_query

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["voice_id"], "heygen_cloud_voice_001")
        self.assertEqual(records["clones"][0]["voice_id"], "heygen_cloud_voice_001")
        self.assertEqual(candidates["candidates"][0]["provider"], "heygen")
        self.assertEqual(candidates["candidates"][0]["voice_source"], "cloud_clone")

    def test_minimax_clone_record_is_promoted_to_tts_candidate(self) -> None:
        voice_cloning = load_voice_cloning_module()

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            record = {
                "provider": "minimax",
                "target_model": "minimax-voice-clone-v1",
                "voice_id": "ocadv_minimax_001",
                "voice": "ocadv_minimax_001",
                "voice_source": "cloud_clone",
                "reference_audio_sha256": "abc789",
                "created_at": "2026-06-22T00:00:00Z",
            }

            candidate = voice_cloning.upsert_clone_candidate(workspace, record)

        self.assertEqual(candidate["provider"], "minimax")
        self.assertEqual(candidate["model"], "minimax-voice-clone-v1")
        self.assertEqual(candidate["voice_id"], "ocadv_minimax_001")
        self.assertEqual(candidate["source_clone_provider"], "minimax")

    def test_heygen_provider_lists_private_voices_and_deletes_voice(self) -> None:
        from ToolLibrary.Analysis_V1.tts_quick_adv.providers import heygen_voice_clone

        calls: list[tuple[str, str]] = []
        delete_requested = False
        old_get = heygen_voice_clone.get_json
        old_delete = heygen_voice_clone.delete_json

        def fake_get(url: str, api_key: str, timeout: int = 30) -> dict:
            nonlocal delete_requested
            calls.append(("GET", url))
            self.assertEqual(api_key, "heygen-key")
            voices = [
                {"voice_id": "voice_001", "name": "ocadv_a", "language": "Chinese", "gender": "male"},
                {"voice_id": "voice_002", "name": "manual_voice", "language": "Chinese", "gender": "female"},
            ]
            if delete_requested:
                voices = [item for item in voices if item["voice_id"] != "voice_001"]
            return {
                "data": voices,
                "has_more": False,
                "next_token": None,
            }

        def fake_delete(url: str, api_key: str, timeout: int = 30) -> dict:
            nonlocal delete_requested
            calls.append(("DELETE", url))
            self.assertEqual(api_key, "heygen-key")
            delete_requested = True
            return {"data": {"voice_id": "voice_001"}}

        heygen_voice_clone.get_json = fake_get
        heygen_voice_clone.delete_json = fake_delete
        try:
            listed = heygen_voice_clone.list_voices(api_key="heygen-key", prefix="ocadv", page_size=100)
            deleted = heygen_voice_clone.delete_voice(api_key="heygen-key", voice_id="voice_001")
        finally:
            heygen_voice_clone.get_json = old_get
            heygen_voice_clone.delete_json = old_delete

        self.assertEqual(listed["provider"], "heygen")
        self.assertEqual(listed["count"], 2)
        self.assertEqual(listed["voices"][0]["voice_id"], "voice_001")
        self.assertEqual(listed["voices"][1]["voice_id"], "voice_002")
        self.assertTrue(deleted["deleted"])
        self.assertTrue(deleted["delete_confirmed"])
        self.assertIn("/v3/voices?", calls[0][1])
        self.assertIn("type=private", calls[0][1])
        self.assertTrue(calls[1][1].endswith("/v3/voices/voice_001"))
        self.assertIn("/v3/voices?", calls[2][1])

    def test_heygen_delete_reports_unconfirmed_when_voice_still_lists(self) -> None:
        from ToolLibrary.Analysis_V1.tts_quick_adv.providers import heygen_voice_clone

        old_delays = heygen_voice_clone.DELETE_CONFIRMATION_DELAYS
        old_get = heygen_voice_clone.get_json
        old_delete = heygen_voice_clone.delete_json

        def fake_get(url: str, api_key: str, timeout: int = 30) -> dict:
            return {
                "data": [{"voice_id": "voice_still_present", "name": "ocadv_still"}],
                "has_more": False,
                "next_token": None,
            }

        def fake_delete(url: str, api_key: str, timeout: int = 30) -> dict:
            return {"data": {"voice_id": "voice_still_present"}}

        heygen_voice_clone.DELETE_CONFIRMATION_DELAYS = (0.0,)
        heygen_voice_clone.get_json = fake_get
        heygen_voice_clone.delete_json = fake_delete
        try:
            deleted = heygen_voice_clone.delete_voice(api_key="heygen-key", voice_id="voice_still_present")
        finally:
            heygen_voice_clone.DELETE_CONFIRMATION_DELAYS = old_delays
            heygen_voice_clone.get_json = old_get
            heygen_voice_clone.delete_json = old_delete

        self.assertFalse(deleted["deleted"])
        self.assertFalse(deleted["delete_confirmed"])
        self.assertIn("still returned", deleted["message"])

    def test_clone_delete_removes_current_workspace_local_heygen_records(self) -> None:
        voice_cloning = load_voice_cloning_module()
        old_delete = voice_cloning.heygen_voice_clone.delete_voice

        def fake_delete(**kwargs: object) -> dict[str, object]:
            return {"provider": "heygen", "voice_id": kwargs.get("voice_id"), "deleted": True}

        voice_cloning.heygen_voice_clone.delete_voice = fake_delete
        try:
            with tempfile.TemporaryDirectory() as tmp:
                workspace = Path(tmp)
                clone_record = {
                    "provider": "heygen",
                    "target_model": "heygen-voice-clone-v3",
                    "voice_id": "voice_delete_001",
                    "voice": "voice_delete_001",
                    "voice_source": "cloud_clone",
                    "reference_audio_sha256": "sha-delete",
                }
                records_path = workspace / "SessionOutput" / "tts" / "cloud_voice_clones.json"
                records_path.parent.mkdir(parents=True, exist_ok=True)
                records_path.write_text(json.dumps({"clones": [clone_record]}), encoding="utf-8")
                candidate = voice_cloning.clone_candidate_from_record(clone_record)
                candidates_path = workspace / "SessionOutput" / "tts" / "tts_builder_candidates.json"
                candidates_path.write_text(json.dumps({"selected_candidate_id": candidate["candidate_id"], "selected_candidate": candidate, "candidates": [candidate]}), encoding="utf-8")
                args = voice_cloning.CloneVoiceArgs(
                    provider="heygen",
                    api_key="heygen-key",
                    api_key_env="",
                    workspace_id="",
                    enrollment_model="voice-enrollment",
                    target_model="heygen-voice-clone-v3",
                    prefix="ocadv",
                    audio_path="",
                    audio_url="",
                    language_hints="zh",
                    max_prompt_audio_length=16.0,
                    page_index=0,
                    page_size=100,
                    voice_id="voice_delete_001",
                    consent_confirmed=True,
                    consent_actor="contract",
                    consent_note="",
                    force=False,
                )

                payload = voice_cloning.delete_cloned_voice(workspace, args)
                records = json.loads(records_path.read_text(encoding="utf-8"))
                candidates = json.loads(candidates_path.read_text(encoding="utf-8"))
        finally:
            voice_cloning.heygen_voice_clone.delete_voice = old_delete

        self.assertTrue(payload["deleted"])
        self.assertEqual(payload["removed_records"], 1)
        self.assertEqual(payload["removed_candidates"], 1)
        self.assertEqual(records["clones"], [])
        self.assertEqual(candidates["candidates"], [])
        self.assertEqual(candidates["selected_candidate_id"], "")

    def test_clone_delete_does_not_cleanup_when_provider_does_not_confirm_delete(self) -> None:
        voice_cloning = load_voice_cloning_module()
        old_delete = voice_cloning.heygen_voice_clone.delete_voice

        def fake_delete(**kwargs: object) -> dict[str, object]:
            return {
                "provider": "heygen",
                "voice_id": kwargs.get("voice_id"),
                "delete_requested": True,
                "deleted": False,
                "delete_confirmed": False,
                "message": "HeyGen did not confirm deletion for voice_id voice_delete_001. The voice is still returned by /v3/voices.",
            }

        voice_cloning.heygen_voice_clone.delete_voice = fake_delete
        try:
            with tempfile.TemporaryDirectory() as tmp:
                workspace = Path(tmp)
                clone_record = {
                    "provider": "heygen",
                    "target_model": "heygen-voice-clone-v3",
                    "voice_id": "voice_delete_001",
                    "voice": "voice_delete_001",
                    "voice_source": "cloud_clone",
                    "reference_audio_sha256": "sha-delete",
                }
                records_path = workspace / "SessionOutput" / "tts" / "cloud_voice_clones.json"
                records_path.parent.mkdir(parents=True, exist_ok=True)
                records_path.write_text(json.dumps({"clones": [clone_record]}), encoding="utf-8")
                args = voice_cloning.CloneVoiceArgs(
                    provider="heygen",
                    api_key="heygen-key",
                    api_key_env="",
                    workspace_id="",
                    enrollment_model="voice-enrollment",
                    target_model="heygen-voice-clone-v3",
                    prefix="ocadv",
                    audio_path="",
                    audio_url="",
                    language_hints="zh",
                    max_prompt_audio_length=16.0,
                    page_index=0,
                    page_size=100,
                    voice_id="voice_delete_001",
                    consent_confirmed=True,
                    consent_actor="contract",
                    consent_note="",
                    force=False,
                )

                with self.assertRaises(voice_cloning.CloudCloneError) as raised:
                    voice_cloning.delete_cloned_voice(workspace, args)
                records = json.loads(records_path.read_text(encoding="utf-8"))
        finally:
            voice_cloning.heygen_voice_clone.delete_voice = old_delete

        self.assertEqual(raised.exception.code, "clone_delete_not_confirmed")
        self.assertEqual(records["clones"], [clone_record])

    def test_minimax_provider_module_resolves_and_skips_public_url(self) -> None:
        voice_cloning = load_voice_cloning_module()

        self.assertEqual(voice_cloning.normalized_clone_provider("minimax"), "minimax")
        self.assertEqual(voice_cloning.clone_provider_for_target_model("minimax-voice-clone-v1"), "minimax")
        module = voice_cloning.provider_module("minimax")
        self.assertEqual(module.PROVIDER_ID, "minimax")
        # MiniMax uploads the local reference file directly, so the clone flow must
        # not require publishing the audio to a public R2 URL.
        self.assertFalse(getattr(module, "REQUIRES_PUBLIC_AUDIO_URL", True))
        for name in ("create_voice", "list_voices", "query_voice", "delete_voice"):
            self.assertTrue(callable(getattr(module, name, None)), name)

    def test_clone_voice_publishes_local_audio_to_public_https_url(self) -> None:
        voice_cloning = load_voice_cloning_module()

        calls: dict[str, str] = {}
        old_publish = voice_cloning.publish_clone_audio_url
        old_create = voice_cloning.aliyun_voice_clone.create_voice
        old_upload = voice_cloning.aliyun_voice_clone.dashscope_upload_file

        def fake_publish(audio_path: Path, reference_audio_sha256: str) -> str:
            calls["published_path"] = audio_path.name
            calls["published_sha"] = reference_audio_sha256
            return "https://assets.example.test/reference.wav?X-Amz-Signature=secret"

        def fake_create(**kwargs: object) -> dict[str, object]:
            calls["audio_url"] = str(kwargs.get("audio_url") or "")
            return {"provider": "aliyun_dashscope", "voice_id": "octest_voice_001", "request_id": "req_001"}

        def fail_upload(*_args: object, **_kwargs: object) -> str:
            raise AssertionError("R2 publishing should avoid DashScope oss:// upload fallback")

        voice_cloning.publish_clone_audio_url = fake_publish
        voice_cloning.aliyun_voice_clone.create_voice = fake_create
        voice_cloning.aliyun_voice_clone.dashscope_upload_file = fail_upload
        try:
            with tempfile.TemporaryDirectory() as tmp:
                workspace = Path(tmp)
                audio_path = workspace / "SessionOutput" / "Audio_Reference.wav"
                audio_path.parent.mkdir(parents=True, exist_ok=True)
                audio_path.write_bytes(b"RIFF....WAVEfmt ")
                args = voice_cloning.CloneVoiceArgs(
                    provider="aliyun",
                    api_key="fake-dashscope-key",
                    api_key_env="",
                    workspace_id="",
                    enrollment_model="voice-enrollment",
                    target_model="cosyvoice-v3.5-flash",
                    prefix="octest",
                    audio_path="",
                    audio_url="",
                    language_hints="zh",
                    max_prompt_audio_length=16.0,
                    page_index=0,
                    page_size=10,
                    voice_id="",
                    consent_confirmed=True,
                    consent_actor="contract",
                    consent_note="synthetic test fixture",
                    force=False,
                )

                payload = voice_cloning.clone_voice(workspace, args)

        finally:
            voice_cloning.publish_clone_audio_url = old_publish
            voice_cloning.aliyun_voice_clone.create_voice = old_create
            voice_cloning.aliyun_voice_clone.dashscope_upload_file = old_upload

        self.assertEqual(calls["audio_url"], "https://assets.example.test/reference.wav?X-Amz-Signature=secret")
        self.assertEqual(payload["voice_id"], "octest_voice_001")
        self.assertEqual(payload["audio_url"], "")
        self.assertTrue(payload["published_to_public_assets"])
        self.assertEqual(payload["audio_publish_provider"], "r2")

    def test_public_asset_r2_env_file_is_loaded_for_clone_audio_publish(self) -> None:
        voice_cloning = load_voice_cloning_module()
        env_keys = set(voice_cloning.PUBLIC_ASSET_R2_ENV_KEYS) | {
            "OPENCREW_PUBLIC_ASSETS_R2_ENV",
            "OPENCREW_PUBLIC_ASSET_R2_ENV",
            "OPENCREW_DATA_DIR",
        }
        old_env = {key: os.environ.get(key) for key in env_keys}
        old_put = voice_cloning.r2_put_object
        calls: dict[str, str] = {}

        def fake_put(endpoint: str, bucket: str, object_key: str, *_args: object) -> None:
            calls["endpoint"] = endpoint
            calls["bucket"] = bucket
            calls["object_key"] = object_key

        try:
            for key in env_keys:
                os.environ.pop(key, None)
            with tempfile.TemporaryDirectory() as tmp:
                tmp_path = Path(tmp)
                env_path = tmp_path / "public_assets_r2.env"
                env_path.write_text(
                    "\n".join([
                        "OPENCREW_PUBLIC_ASSET_R2_ENDPOINT=https://assets.example.test",
                        "OPENCREW_PUBLIC_ASSET_R2_BUCKET=opencrew-test",
                        "OPENCREW_PUBLIC_ASSET_R2_REGION=auto",
                        "OPENCREW_PUBLIC_ASSET_R2_PREFIX=clone-test",
                        "OPENCREW_PUBLIC_ASSET_R2_ACCESS_KEY_ID=test-access",
                        "OPENCREW_PUBLIC_ASSET_R2_SECRET_ACCESS_KEY=test-secret",
                    ]),
                    encoding="utf-8",
                )
                audio_path = tmp_path / "Audio_Reference.wav"
                audio_path.write_bytes(b"RIFF....WAVEfmt ")
                os.environ["OPENCREW_PUBLIC_ASSETS_R2_ENV"] = str(env_path)
                voice_cloning.r2_put_object = fake_put

                url = voice_cloning.publish_clone_audio_url(audio_path, "abc123")

            self.assertTrue(url.startswith("https://assets.example.test/opencrew-test/clone-test/"))
            self.assertEqual(calls["endpoint"], "https://assets.example.test")
            self.assertEqual(calls["bucket"], "opencrew-test")
            self.assertTrue(calls["object_key"].startswith("clone-test/"))
        finally:
            voice_cloning.r2_put_object = old_put
            for key in env_keys:
                if old_env[key] is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = old_env[key] or ""

    def test_clone_voice_rejects_non_http_audio_url_before_provider_call(self) -> None:
        voice_cloning = load_voice_cloning_module()
        old_publish = voice_cloning.publish_clone_audio_url
        old_create = voice_cloning.aliyun_voice_clone.create_voice

        def fake_publish(*_args: object, **_kwargs: object) -> str:
            return "oss://dashscope-private/reference.wav"

        def fail_create(**_kwargs: object) -> dict[str, object]:
            raise AssertionError("non-http audio URLs must not reach the clone provider")

        voice_cloning.publish_clone_audio_url = fake_publish
        voice_cloning.aliyun_voice_clone.create_voice = fail_create
        try:
            with tempfile.TemporaryDirectory() as tmp:
                workspace = Path(tmp)
                audio_path = workspace / "SessionOutput" / "Audio_Reference.wav"
                audio_path.parent.mkdir(parents=True, exist_ok=True)
                audio_path.write_bytes(b"RIFF....WAVEfmt ")
                args = voice_cloning.CloneVoiceArgs(
                    provider="aliyun",
                    api_key="fake-dashscope-key",
                    api_key_env="",
                    workspace_id="",
                    enrollment_model="voice-enrollment",
                    target_model="cosyvoice-v3.5-flash",
                    prefix="octest",
                    audio_path="",
                    audio_url="",
                    language_hints="zh",
                    max_prompt_audio_length=16.0,
                    page_index=0,
                    page_size=10,
                    voice_id="",
                    consent_confirmed=True,
                    consent_actor="contract",
                    consent_note="synthetic test fixture",
                    force=False,
                )

                with self.assertRaises(voice_cloning.CloudCloneError) as ctx:
                    voice_cloning.clone_voice(workspace, args)

            self.assertEqual(ctx.exception.code, "clone_audio_url_invalid")
        finally:
            voice_cloning.publish_clone_audio_url = old_publish
            voice_cloning.aliyun_voice_clone.create_voice = old_create

    def test_cloud_clone_default_target_model_stays_on_cosyvoice_flash(self) -> None:
        provider_source = CLONE_PROVIDER_PATH.read_text(encoding="utf-8")
        cli_source = CLI_PATH.read_text(encoding="utf-8")

        self.assertIn('DEFAULT_TARGET_MODEL = "cosyvoice-v3.5-flash"', provider_source)
        self.assertIn('DEFAULT_CLONE_TARGET_MODEL = "cosyvoice-v3.5-flash"', cli_source)

    @unittest.skipUnless(ffmpeg_available(), "ffmpeg is required for reference audio extraction")
    def test_offline_smoke_produces_reference_ranking_and_state_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            catalog_dir = workspace / "VoiceCatalog" / DEFAULT_MODEL
            write_smoke_workspace(workspace)
            write_smoke_catalog(catalog_dir)

            catalog_completed = run_quick_adv_cli(workspace, "catalog-list", catalog_dir, "--final-count", "1")
            self.assertEqual(catalog_completed.returncode, 0, catalog_completed.stderr or catalog_completed.stdout)
            catalog_payload = json.loads(catalog_completed.stdout)
            self.assertTrue(catalog_payload["ok"])
            self.assertEqual(catalog_payload["count"], 3)

            sample_completed = run_quick_adv_cli(
                workspace,
                "sample-reference",
                catalog_dir,
                "--reference-start",
                "0",
                "--reference-duration",
                "4",
                "--final-count",
                "1",
            )
            self.assertEqual(sample_completed.returncode, 0, sample_completed.stderr or sample_completed.stdout)
            sample_payload = json.loads(sample_completed.stdout)
            self.assertTrue(sample_payload["ok"])
            self.assertGreater(sample_payload["sampling_audit"]["sampling_score"], 0)
            self.assertIn("vad", sample_payload["sampling_audit"])
            self.assertIn("coverage", sample_payload["sampling_audit"])
            self.assertIn("consonant_integrity_score", sample_payload["sampling_audit"]["score_parts"])
            self.assertIn("sampling_metrics", sample_payload["reference_profile"])

            rank_completed = run_quick_adv_cli(
                workspace,
                "rank",
                catalog_dir,
                "--disable-speechbrain",
                "--reference-start",
                "0",
                "--reference-duration",
                "4",
                "--stage1-count",
                "3",
                "--stage2-count",
                "3",
                "--final-count",
                "1",
            )
            self.assertEqual(rank_completed.returncode, 0, rank_completed.stderr or rank_completed.stdout)
            rank_payload = json.loads(rank_completed.stdout)
            self.assertTrue(rank_payload["ok"])
            self.assertEqual(rank_payload["scoring_mode"], "degraded_resemblyzer_acoustic")
            self.assertEqual(rank_payload["score_schema_version"], "quick_adv_score_v2")
            self.assertEqual(rank_payload["ranking_strategy"], "two_stage_high_recall")
            self.assertEqual(rank_payload["stage_counts"]["catalog_total"], 3)
            self.assertEqual(rank_payload["stage_counts"]["stage1_pool"], 3)
            self.assertEqual(len(rank_payload["recommended"]), 1)
            recommended = rank_payload["recommended"][0]
            self.assertIn("match_score", recommended)
            self.assertIn("scores", recommended)
            self.assertIn("stage1_rank", recommended)
            self.assertIn("stage2_rank", recommended)
            self.assertIn("dimension_scores", recommended)
            self.assertIn("raw_scores", recommended)
            self.assertIn("penalties", recommended)
            self.assertIn("explanation", recommended)
            self.assertIn("timbre_score", recommended["dimension_scores"])
            self.assertNotIn("timbre_rank_component", recommended["dimension_scores"])
            self.assertIn("timbre_rank_component", recommended["score_parts"])
            self.assertIn("catalog_quality_penalty", recommended["penalties"])
            self.assertNotIn("catalog_quality_score", recommended["score_parts"])

            expected_outputs = [
                "S5_03_03_TTSBuilderQuickAdv/Working/Audio_Reference_Selected.wav",
                "S5_03_03_TTSBuilderQuickAdv/Output/reference_voice_profile.json",
                "S5_03_03_TTSBuilderQuickAdv/Output/reference_sampling_audit.json",
                "S5_03_03_TTSBuilderQuickAdv/Output/catalog_stage1_resemblyzer.json",
                "S5_03_03_TTSBuilderQuickAdv/Output/catalog_stage2_speechbrain.json",
                "S5_03_03_TTSBuilderQuickAdv/Interactive/ranking_board.json",
            ]
            for rel in expected_outputs:
                self.assertTrue((workspace / rel).is_file(), rel)

            state_completed = run_quick_adv_cli(workspace, "state", catalog_dir)
            self.assertEqual(state_completed.returncode, 0, state_completed.stderr or state_completed.stdout)
            state_payload = json.loads(state_completed.stdout)
            self.assertTrue(state_payload["ok"])
            self.assertTrue(state_payload["reference"]["profile_exists"])
            self.assertEqual(state_payload["ranking_board"]["scoring_mode"], "degraded_resemblyzer_acoustic")
            self.assertEqual(state_payload["final_candidates"], None)
            self.assertTrue((workspace / "S5_03_03_TTSBuilderQuickAdv/Interactive/state.json").is_file())

    @unittest.skipUnless(ffmpeg_available(), "ffmpeg is required for reference audio extraction")
    def test_qwen_catalog_is_supported_for_advanced_ranking(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            catalog_dir = workspace / "VoiceCatalog" / QWEN_MODEL
            write_smoke_workspace(workspace)
            write_smoke_catalog(catalog_dir, provider="qwen", model=QWEN_MODEL)

            catalog_completed = run_quick_adv_cli(workspace, "catalog-list", catalog_dir, "--providers", "qwen", "--model", QWEN_MODEL, "--final-count", "1")
            self.assertEqual(catalog_completed.returncode, 0, catalog_completed.stderr or catalog_completed.stdout)
            catalog_payload = json.loads(catalog_completed.stdout)
            self.assertTrue(catalog_payload["ok"])
            self.assertEqual(catalog_payload["provider"], "qwen")
            self.assertEqual(catalog_payload["model"], "qwen3-tts-flash")

            rank_completed = run_quick_adv_cli(
                workspace,
                "rank",
                catalog_dir,
                "--providers",
                "qwen",
                "--model",
                QWEN_MODEL,
                "--disable-speechbrain",
                "--reference-start",
                "0",
                "--reference-duration",
                "4",
                "--stage1-count",
                "3",
                "--stage2-count",
                "3",
                "--final-count",
                "1",
            )
            self.assertEqual(rank_completed.returncode, 0, rank_completed.stderr or rank_completed.stdout)
            rank_payload = json.loads(rank_completed.stdout)
            self.assertTrue(rank_payload["ok"])
            self.assertEqual(rank_payload["recommended"][0]["provider"], "qwen")
            self.assertEqual(rank_payload["recommended"][0]["model"], QWEN_MODEL)
            self.assertEqual(rank_payload["ranking_strategy"], "two_stage_high_recall")

    @unittest.skipUnless(ffmpeg_available(), "ffmpeg is required for reference audio extraction")
    def test_bytedance_catalog_is_supported_for_advanced_ranking(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            catalog_dir = workspace / "VoiceCatalog" / BYTEDANCE_MODEL
            write_smoke_workspace(workspace)
            write_smoke_catalog(catalog_dir, provider="bytedance", model=BYTEDANCE_MODEL)

            catalog_completed = run_quick_adv_cli(workspace, "catalog-list", catalog_dir, "--providers", "bytedance", "--model", BYTEDANCE_MODEL, "--final-count", "1")
            self.assertEqual(catalog_completed.returncode, 0, catalog_completed.stderr or catalog_completed.stdout)
            catalog_payload = json.loads(catalog_completed.stdout)
            self.assertTrue(catalog_payload["ok"])
            self.assertEqual(catalog_payload["provider"], "bytedance")
            self.assertEqual(catalog_payload["model"], BYTEDANCE_MODEL)

            rank_completed = run_quick_adv_cli(
                workspace,
                "rank",
                catalog_dir,
                "--providers",
                "bytedance",
                "--model",
                BYTEDANCE_MODEL,
                "--disable-speechbrain",
                "--reference-start",
                "0",
                "--reference-duration",
                "4",
                "--stage1-count",
                "3",
                "--stage2-count",
                "3",
                "--final-count",
                "1",
            )
            self.assertEqual(rank_completed.returncode, 0, rank_completed.stderr or rank_completed.stdout)
            rank_payload = json.loads(rank_completed.stdout)
            self.assertTrue(rank_payload["ok"])
            self.assertEqual(rank_payload["recommended"][0]["provider"], "bytedance")
            self.assertEqual(rank_payload["recommended"][0]["model"], BYTEDANCE_MODEL)
            self.assertEqual(rank_payload["ranking_strategy"], "two_stage_high_recall")

    def test_default_qwen_catalog_assets_are_present_for_deployment(self) -> None:
        catalog_dir = REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "VoiceCatalog" / QWEN_MODEL
        index_path = catalog_dir / "voice_catalog_index.json"

        self.assertTrue(index_path.is_file(), "Qwen catalog index must be committed or generated before deployment.")
        payload = json.loads(index_path.read_text(encoding="utf-8"))
        voices = payload.get("voices") if isinstance(payload.get("voices"), list) else []
        self.assertEqual(payload.get("provider"), "qwen")
        self.assertEqual(payload.get("model"), QWEN_MODEL)
        self.assertGreaterEqual(len(voices), 48)
        for item in voices:
            rel = str(item.get("sample_audio_path") or item.get("audio", {}).get("path") or "").strip()
            self.assertTrue(rel, item)
            sample_path = catalog_dir / rel
            self.assertTrue(sample_path.is_file(), rel)
            self.assertGreater(sample_path.stat().st_size, 1024, rel)

    def test_default_bytedance_catalog_assets_are_present_for_deployment(self) -> None:
        catalog_dir = REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "VoiceCatalog" / BYTEDANCE_MODEL
        index_path = catalog_dir / "voice_catalog_index.json"

        self.assertTrue(index_path.is_file(), "ByteDance catalog index must be committed or generated before deployment.")
        payload = json.loads(index_path.read_text(encoding="utf-8"))
        voices = payload.get("voices") if isinstance(payload.get("voices"), list) else []
        self.assertEqual(payload.get("provider"), "bytedance")
        self.assertEqual(payload.get("model"), BYTEDANCE_MODEL)
        self.assertGreaterEqual(len(voices), 100)
        for item in voices:
            rel = str(item.get("sample_audio_path") or item.get("audio", {}).get("path") or "").strip()
            self.assertTrue(rel, item)
            sample_path = catalog_dir / rel
            self.assertTrue(sample_path.is_file(), rel)
            self.assertGreater(sample_path.stat().st_size, 1024, rel)

    def test_degraded_stage2_formula_is_defined_without_speechbrain(self) -> None:
        scoring = load_scoring_module()
        score = scoring.build_stage2_score(
            scoring_mode=scoring.SCORING_DEGRADED,
            stage1_score=80.0,
            resemblyzer_score=0.72,
            speechbrain_score=None,
            pitch_score=70.0,
            pace_score=60.0,
            brightness_score=90.0,
            energy_score=50.0,
            clarity_score=40.0,
            stability_score=100.0,
        )

        self.assertGreater(score, 0)
        self.assertLessEqual(score, 100)
        self.assertEqual(scoring.SCORING_DEGRADED, "degraded_resemblyzer_acoustic")
        self.assertEqual(scoring.SCORE_SCHEMA_VERSION, "quick_adv_score_v2")

    def test_score_v2_separates_rank_timbre_from_display_timbre(self) -> None:
        scoring = load_scoring_module()

        rank_component = scoring.build_timbre_rank_component(
            resemblyzer_score=0.62,
            brightness_score=80.0,
            roughness_score=60.0,
        )
        display_timbre = scoring.build_timbre_score(
            scoring_mode=scoring.SCORING_FULL,
            resemblyzer_score=0.62,
            speechbrain_score=0.84,
            texture_score=75.0,
        )

        self.assertGreater(rank_component, 0)
        self.assertGreater(display_timbre, 0)
        self.assertNotEqual(round(rank_component, 3), round(display_timbre, 3))

    def test_proxy_scores_distinguish_similarity_and_absolute_quality(self) -> None:
        scoring = load_scoring_module()

        self.assertGreater(scoring.ratio_score(0.35, 0.34), scoring.ratio_score(0.35, 0.08))
        penalty = scoring.quality_penalty_score(clipping_risk=0.3, silence_ratio=0.4, duration_error=0.5, rms=0.005)
        self.assertGreater(penalty, 0)
        self.assertLessEqual(penalty, 100)

    def test_persona_score_fallbacks_to_neutral_when_age_missing(self) -> None:
        scoring = load_scoring_module()

        self.assertEqual(scoring.build_age_proxy_score("", ""), 50.0)
        self.assertEqual(scoring.build_pitch_band_score(0.0, 0.0), 50.0)
        self.assertGreater(scoring.build_persona_score(gender_score=100.0), 0)

    def test_rank_uses_stage1_pool_before_speechbrain(self) -> None:
        source = CORE_PATH.read_text(encoding="utf-8")

        stage1_index = source.index("stage1 = select_stage1_pool")
        speechbrain_index = source.index("speechbrain_backend = None if args.disable_speechbrain else quick02.load_speechbrain_backend")
        self.assertLess(stage1_index, speechbrain_index)

    def test_stage1_pool_preserves_protected_lane_candidates(self) -> None:
        core = load_core_module()
        rows = []
        for index in range(8):
            rows.append({
                "provider": "google",
                "model": DEFAULT_MODEL,
                "voice": f"top_{index}",
                "stage1_score": 95.0 - index,
                "dimension_scores": {"pitch_score": 40.0, "pace_score": 40.0, "persona_score": 40.0},
            })
        rows.extend([
            {"provider": "qwen", "model": QWEN_MODEL, "voice": "pitch_lane", "stage1_score": 10.0, "dimension_scores": {"pitch_score": 100.0, "pace_score": 10.0, "persona_score": 10.0}},
            {"provider": "google", "model": DEFAULT_MODEL, "voice": "pace_lane", "stage1_score": 9.0, "dimension_scores": {"pitch_score": 10.0, "pace_score": 100.0, "persona_score": 10.0}},
            {"provider": "google", "model": DEFAULT_MODEL, "voice": "persona_lane", "stage1_score": 8.0, "dimension_scores": {"pitch_score": 10.0, "pace_score": 10.0, "persona_score": 100.0}},
        ])

        pool = core.select_stage1_pool(rows, 5)
        voices = {row["voice"]: row for row in pool}

        self.assertIn("pitch_lane", voices)
        self.assertIn("pitch_nearest_top", voices["pitch_lane"]["stage1_lane_sources"])
        self.assertIn("provider_quota_top", voices["pitch_lane"]["stage1_lane_sources"])
        self.assertTrue(voices["pitch_lane"].get("stage1_lane_protected"))

    def test_speechbrain_embedding_failures_fallback_without_crashing(self) -> None:
        core = load_core_module()

        class BrokenSpeechBrainQuick02:
            @staticmethod
            def speechbrain_embedding(_path: Path, _backend: object) -> object:
                raise RuntimeError("bad wav")

        result = {"warnings": []}
        embedding = core.safe_speechbrain_embedding(BrokenSpeechBrainQuick02(), Path("missing.wav"), object(), result, scope="candidate", voice="VoiceA")

        self.assertIsNone(embedding)
        self.assertEqual(result["warnings"][0]["code"], "speechbrain_embedding_failed")

    def test_qwen_audio_download_retries_incomplete_reads(self) -> None:
        core = load_core_module()
        calls = {"count": 0}
        original_build_opener = core.urllib.request.build_opener
        original_sleep = core.time.sleep

        class FakeResponse:
            headers = {"Content-Type": "audio/wav"}

            def __enter__(self):
                return self

            def __exit__(self, _exc_type, _exc, _tb):
                return False

            def read(self) -> bytes:
                calls["count"] += 1
                if calls["count"] == 1:
                    raise http.client.IncompleteRead(b"partial", 3)
                return b"complete-audio"

        class FakeOpener:
            def open(self, _request, timeout: int = 0):
                self.timeout = timeout
                return FakeResponse()

        try:
            core.urllib.request.build_opener = lambda *_args, **_kwargs: FakeOpener()
            core.time.sleep = lambda *_args, **_kwargs: None
            with tempfile.TemporaryDirectory() as tmp:
                output_path = Path(tmp) / "candidate.wav"
                meta = core.write_qwen_audio("https://dashscope.example/audio.wav", "url", output_path, attempts=2)

                self.assertEqual(output_path.read_bytes(), b"complete-audio")
                self.assertEqual(meta["download_attempts"], 2)
                self.assertEqual(calls["count"], 2)
        finally:
            core.urllib.request.build_opener = original_build_opener
            core.time.sleep = original_sleep

    def test_stage2_uses_unpenalized_stage1_prior_before_quality_penalty(self) -> None:
        source = CORE_PATH.read_text(encoding="utf-8")

        self.assertIn('"stage1_base_score"', source)
        self.assertIn('stage1_prior_score = safe_float(row.get("stage1_base_score"), safe_float(row.get("stage1_score")))', source)

    def test_adv_candidates_publish_match_score_as_score_for_existing_ui(self) -> None:
        source = CORE_PATH.read_text(encoding="utf-8")

        self.assertIn('"score": match_score_value', source)
        self.assertIn('"match_score": match_score_value', source)
        self.assertIn('"scoring_mode": ranking.get("scoring_mode")', source)

    def test_run_uses_03_03_ranking_to_generate_candidates(self) -> None:
        source = CORE_PATH.read_text(encoding="utf-8")

        self.assertIn("build_quick_compatible_payload_from_ranking", source)
        self.assertIn("ranking.get(\"recommended\"", source)
        self.assertNotIn("quick02.run_builder", source)

    def test_qwen_generation_path_is_not_gemini_only(self) -> None:
        source = CORE_PATH.read_text(encoding="utf-8")

        self.assertIn("call_qwen_tts", source)
        self.assertIn("load_provider_api_key", source)
        self.assertIn("\"qwen\"", source)

    def test_quick_adv_uses_llm_prompt_planner_before_tts_generation(self) -> None:
        source = CORE_PATH.read_text(encoding="utf-8")

        self.assertIn("build_prompt_planner_request", source)
        self.assertIn("call_prompt_planner", source)
        self.assertIn("normalize_planned_prompt", source)
        self.assertIn("正文：", source)
        self.assertIn("prompt_source", source)
        self.assertIn("llm_prompt_planner", source)
        self.assertIn("rule_fallback", source)
        self.assertIn("prompt_model_call_made", source)

    def test_llm_prompt_planner_output_keeps_tts_body_marker(self) -> None:
        core = load_core_module()

        prompt = core.normalize_planned_prompt("请用沉稳自然的口播语气，只朗读正文。", "这是原始正文。")

        self.assertIn("正文：\n这是原始正文。", prompt)
        self.assertEqual(core.normalize_planned_prompt(prompt, "这是原始正文。"), prompt)

    def test_openclip_preview_sanitizes_cosyvoice_text_and_instruction(self) -> None:
        router_source = OPENCLIP_ROUTER_PATH.read_text(encoding="utf-8")
        schemas_source = OPENCLIP_SCHEMAS_PATH.read_text(encoding="utf-8")

        self.assertIn("extract_analysis_v1_tts_preview_text", router_source)
        self.assertIn("strip_analysis_v1_tts_preview_instruction", router_source)
        self.assertIn("sample_text = extract_analysis_v1_tts_preview_text(payload.text, prompt)", router_source)
        self.assertIn("complex_prompt = strip_analysis_v1_tts_preview_instruction(prompt)", router_source)
        self.assertIn("当前\\s*voice", router_source)
        self.assertIn("language = (payload.language or \"zh\").strip() or \"zh\"", router_source)
        self.assertIn("language: str = \"zh\"", schemas_source)

    def test_cloud_clone_preview_resolves_real_model_after_customer_redaction(self) -> None:
        router_source = OPENCLIP_ROUTER_PATH.read_text(encoding="utf-8")
        schemas_source = OPENCLIP_SCHEMAS_PATH.read_text(encoding="utf-8")
        component_source = ANALYSIS_V1_TTS_BUILDER_COMPONENT_PATH.read_text(encoding="utf-8")

        for token in (
            "target_model: str = \"\"",
            "voice_source: str = \"\"",
            "source_clone_provider: str = \"\"",
        ):
            self.assertIn(token, schemas_source)
        for token in (
            "def analysis_v1_cloud_clone_preview_defaults",
            "def analysis_v1_clone_payload_model",
            "voice_source == \"cloud_clone\" or candidate_id.startswith(\"clone_\")",
            "cloud_voice_clones.json",
            "tts_builder_candidates.json",
            "clone_defaults = analysis_v1_cloud_clone_preview_defaults(workspace, payload)",
            "else:\n            provider, model = resolve_tts_public_alias(ctx, payload.provider or \"\", payload.model or \"\")",
            "provider = normalize_analysis_v1_clone_provider(provider) or provider",
        ):
            self.assertIn(token, router_source)
        preview_section = router_source.split("async def preview_analysis_v1_tts", 1)[1].split("voice_id = (payload.voice_id or \"\").strip()", 1)[0]
        self.assertLess(preview_section.index("clone_defaults = analysis_v1_cloud_clone_preview_defaults(workspace, payload)"), preview_section.index("resolve_tts_public_alias"))
        for token in (
            "function isRedactedModelField",
            "function publicModelField",
            "voice_source: isClone ? \"cloud_clone\"",
            "source_clone_provider: isClone ? normalizeCloneProvider",
            "if (isCloudCloneCandidate(item))",
        ):
            self.assertIn(token, component_source)

    def test_talking_head_clone_preview_resolves_runtime_from_public_voice_alias(self) -> None:
        router_source = OPENCLIP_ROUTER_PATH.read_text(encoding="utf-8")
        modal_source = TALKING_HEAD_CREATE_MODAL_PATH.read_text(encoding="utf-8")

        preview_payload = modal_source.split("const result = await props.onPreviewVoiceClone({", 1)[1].split("});", 1)[0]
        self.assertIn('voice_source: "cloud_clone"', preview_payload)
        self.assertNotIn("provider:", preview_payload)
        self.assertNotIn("source_clone_provider:", preview_payload)
        self.assertNotIn("model:", preview_payload)
        self.assertNotIn("target_model:", preview_payload)
        self.assertNotIn("function cloudVoiceProvider", modal_source)
        self.assertNotIn("function cloudVoiceModel", modal_source)
        preview_section = router_source.split("async def preview_analysis_v1_clone_tts_without_task", 1)[1].split(
            'raise HTTPException(status_code=400, detail="prompt is required")',
            1,
        )[0]
        self.assertIn("voice_target = resolve_tts_voice_alias(ctx, payload.voice_id)", preview_section)
        self.assertIn('(voice_target or {}).get("provider")', preview_section)
        self.assertIn('(voice_target or {}).get("model")', preview_section)
        self.assertIn('clone_preview_providers = {"heygen", "cosyvoice", "minimax"}', preview_section)
        self.assertIn("fallback_clone_config = analysis_v1_voice_clone_config()", preview_section)

    def test_cloud_clone_preview_defaults_read_workspace_clone_records_after_redaction(self) -> None:
        from opcrew_backend.koubo.router import analysis_v1_cloud_clone_preview_defaults_from_workspace
        from opcrew_backend.koubo.schemas import OpenClipTTSPreviewPayload

        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            tts_dir = workspace / "SessionOutput" / "tts"
            tts_dir.mkdir(parents=True)
            (tts_dir / "cloud_voice_clones.json").write_text(
                json.dumps(
                    {
                        "clones": [
                            {
                                "voice_id": "voice_hg_001",
                                "candidate_id": "clone_hg_001",
                                "provider": "heygen",
                                "target_model": "heygen-voice-clone-v3",
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            payload = OpenClipTTSPreviewPayload(
                provider="",
                model="",
                source_clone_provider="[model]",
                target_model="[model]",
                voice_source="cloud_clone",
                voice_id="voice_hg_001",
                candidate_id="clone_hg_001",
                text="试听文本",
            )

            defaults = analysis_v1_cloud_clone_preview_defaults_from_workspace(workspace, payload)

        self.assertEqual(defaults, {"provider": "heygen", "model": "heygen-voice-clone-v3"})
        self.assertNotIn("[model]", set(defaults.values()))

    def test_cloud_clone_preview_defaults_read_workspace_candidate_records_after_redaction(self) -> None:
        from opcrew_backend.koubo.router import analysis_v1_cloud_clone_preview_defaults_from_workspace
        from opcrew_backend.koubo.schemas import OpenClipTTSPreviewPayload

        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            tts_dir = workspace / "SessionOutput" / "tts"
            tts_dir.mkdir(parents=True)
            (tts_dir / "tts_builder_candidates.json").write_text(
                json.dumps(
                    {
                        "candidates": [
                            {
                                "voice_id": "voice_mm_001",
                                "candidate_id": "clone_mm_001",
                                "source_clone_provider": "minimaxi",
                                "target_model": "[model]",
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            payload = OpenClipTTSPreviewPayload(
                provider="",
                model="",
                source_clone_provider="[provider]",
                target_model="[model]",
                voice_id="voice_mm_001",
                candidate_id="clone_mm_001",
                text="试听文本",
            )

            defaults = analysis_v1_cloud_clone_preview_defaults_from_workspace(workspace, payload)

        self.assertEqual(defaults, {"provider": "minimax", "model": "minimax-voice-clone-v1"})
        self.assertNotIn("[model]", set(defaults.values()))

    def test_cloud_clone_list_marks_only_workspace_voice_as_current_task(self) -> None:
        from opcrew_backend.koubo.router import mark_analysis_v1_cloud_clone_task_membership

        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            tts_dir = workspace / "SessionOutput" / "tts"
            tts_dir.mkdir(parents=True)
            (tts_dir / "cloud_voice_clones.json").write_text(
                json.dumps({"clones": [{"voice_id": "voice-local"}]}),
                encoding="utf-8",
            )
            result = {
                "voices": [
                    {"voice_id": "voice-local", "target_model": "cosyvoice-v3.5-plus"},
                    {"voice_id": "voice-cloud", "target_model": "cosyvoice-v3.5-plus"},
                ]
            }

            marked = mark_analysis_v1_cloud_clone_task_membership(workspace, result)

        self.assertEqual([item["in_current_task"] for item in marked["voices"]], [True, False])

    def test_quick_adv_state_hides_clones_from_inactive_provider(self) -> None:
        from opcrew_backend.koubo.router import filter_analysis_v1_quick_adv_clones

        result = {
            "cloned_voices": [
                {"voice_id": "old-cosy", "provider": "cosyvoice"},
                {"voice_id": "active-heygen", "source_clone_provider": "heygen"},
            ],
            "final_candidates": {
                "candidates": [
                    {"candidate_id": "normal", "voice_id": "normal", "provider": "google"},
                    {
                        "candidate_id": "clone_old_cosy",
                        "voice_id": "old-cosy",
                        "provider": "cosyvoice",
                        "voice_source": "cloud_clone",
                    },
                    {
                        "candidate_id": "clone_active_heygen",
                        "voice_id": "active-heygen",
                        "provider": "heygen",
                        "voice_source": "cloud_clone",
                    },
                ]
            },
        }

        filtered = filter_analysis_v1_quick_adv_clones(result, "heygen")

        self.assertEqual([item["voice_id"] for item in filtered["cloned_voices"]], ["active-heygen"])
        self.assertEqual(filtered["inactive_clone_count"], 1)
        self.assertEqual(
            [item["candidate_id"] for item in filtered["final_candidates"]["candidates"]],
            ["normal", "clone_active_heygen"],
        )
        self.assertEqual(filtered["inactive_final_candidate_count"], 1)

    def test_quick_adv_state_route_filters_clones_before_returning(self) -> None:
        router_source = OPENCLIP_ROUTER_PATH.read_text(encoding="utf-8")
        route_section = router_source.split('analysis-v1/tts/quick-adv/state"', 1)[1].split('analysis-v1/tts/quick-adv/catalog-list"', 1)[0]

        self.assertIn('clone_config = load_config(ctx, "voice-clone")', route_section)
        self.assertIn("filter_analysis_v1_quick_adv_clones", route_section)

    def test_cloud_clone_prepares_short_reference_audio_before_provider_upload(self) -> None:
        router_source = OPENCLIP_ROUTER_PATH.read_text(encoding="utf-8")
        command_section = router_source.split("def run_analysis_v1_quick_adv_command", 1)[1].split("def system_voice_clone_workspace", 1)[0]

        self.assertIn('command == "clone-voice" and not str(payload.clone_audio_path', command_section)
        self.assertIn('run_analysis_v1_quick_adv_command(workspace, "sample-reference", payload)', command_section)
        self.assertIn('payload.model_copy(update={"clone_audio_path": selected_audio})', command_section)
        self.assertLess(
            command_section.index('run_analysis_v1_quick_adv_command(workspace, "sample-reference", payload)'),
            command_section.index('cmd.extend(["--clone-audio-path", str(payload.clone_audio_path)])'),
        )

    def test_saving_tts_selection_drops_inactive_and_duplicate_cloud_clone_candidates(self) -> None:
        router_source = OPENCLIP_ROUTER_PATH.read_text(encoding="utf-8")
        save_section = router_source.split("def save_analysis_v1_tts_selection_to_variables", 1)[1].split("def wav_data_from_pcm", 1)[0]

        self.assertIn('clone_config = load_config(ctx, "voice-clone")', save_section)
        self.assertIn("selected_provider != active_clone_provider", save_section)
        self.assertIn("row_provider != active_clone_provider", save_section)
        self.assertIn("seen_cloud_voices", save_section)

    def test_openclip_google_tts_preview_retries_without_audio(self) -> None:
        router_source = OPENCLIP_ROUTER_PATH.read_text(encoding="utf-8")

        self.assertIn("analysis_v1_google_tts_retry_prompt", router_source)
        self.assertIn("request_google_tts_payload", router_source)
        self.assertIn("write_first_google_audio", router_source)
        self.assertIn("retry_payload = request_google_tts_payload(retry_prompt, 90)", router_source)
        self.assertIn("primary_response_without_audio", router_source)
        self.assertIn("Google TTS response did not include audio data after retry", router_source)

    def test_tts_builder_g_duration_prefers_ffprobe_for_qwen_placeholder_wav(self) -> None:
        if not ffprobe_available():
            self.skipTest("ffprobe is required for qwen placeholder wav duration probing")
        module = load_tts_builder_g_module()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "qwen_placeholder.wav"
            write_qwen_placeholder_wav(path, duration=1.0)

            with wave.open(str(path), "rb") as reader:
                wave_duration = reader.getnframes() / float(reader.getframerate())

            self.assertGreater(wave_duration, 1000.0)
            self.assertAlmostEqual(module.media_duration(path), 1.0, places=2)

    def test_quick_prompt_tempo_prior_hint_direction_matches_rate_ratio(self) -> None:
        module = load_tts_builder_quick_module()
        scene_profile = {"speaker": "普通中文短视频口播者", "style": "自然口播"}

        faster_prompt = module.build_model_prompt(scene_profile, "Vivian", "测试正文", 1.5, "closest_reference")
        slower_prompt = module.build_model_prompt(scene_profile, "Vivian", "测试正文", 0.7, "closest_reference")

        self.assertIn("相对偏快", faster_prompt)
        self.assertIn("语速略慢", faster_prompt)
        self.assertIn("相对偏慢", slower_prompt)
        self.assertIn("语速略快", slower_prompt)

    def test_aliyun_clone_uses_dashscope_voice_enrollment_service(self) -> None:
        source = CLONE_PROVIDER_PATH.read_text(encoding="utf-8")

        self.assertIn("VoiceEnrollmentService", source)
        self.assertIn("create_voice", source)
        self.assertIn("target_model", source)
        self.assertIn("voice_id", source)

    def test_clone_does_not_fallback_to_generic_tts_key(self) -> None:
        source = VOICE_CLONING_PATH.read_text(encoding="utf-8")

        self.assertIn("DEFAULT_CLONE_API_KEY_ENV = \"DASHSCOPE_API_KEY\"", source)
        self.assertNotIn("OPENCREW_TTS_API_KEY", source)
        self.assertIn("args.enrollment_model", source)
        self.assertIn("clone_consent_required", source)
        self.assertIn("reference_audio_sha256", source)


if __name__ == "__main__":
    unittest.main()
