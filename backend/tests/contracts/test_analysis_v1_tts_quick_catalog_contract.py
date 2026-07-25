from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
import unittest
import wave
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "03_02_TTSBuilderQuick.py"


def load_tts_quick_module():
    spec = importlib.util.spec_from_file_location("analysis_v1_tts_quick_catalog_contract", SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write_silent_wav(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(16000)
        writer.writeframes(b"\x00\x00" * 1600)


class AnalysisV1TTSQuickCatalogContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_tts_quick_module()

    def args(self, voices: str = "", final_count: int = 3, workspace: str = "", force: bool = False):
        return self.module.Args(
            workspace=workspace,
            voice_catalog_dir="",
            provider="google",
            model=self.module.DEFAULT_TTS_MODEL,
            voices=voices,
            reference_start=0.0,
            reference_duration=0.0,
            final_count=final_count,
            database_url="",
            database_url_env="OPENCREW_DATABASE_URL",
            force=force,
            resume=False,
            print_json=False,
        )

    def write_catalog(self, catalog_dir: Path, voices: list[str]) -> None:
        catalog_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "provider": "google",
            "model": self.module.DEFAULT_TTS_MODEL,
            "sample_text_id": self.module.CATALOG_SAMPLE_TEXT_ID,
            "sample_text": "你好，这是系统声音样本。",
            "voices": [
                {
                    "voice": voice,
                    "voice_id": voice,
                    "voice_label": voice,
                    "sample_text_id": self.module.CATALOG_SAMPLE_TEXT_ID,
                    "sample_audio_path": f"{voice}_fixed_cn_v1_16s.wav",
                    "audio": {"path": f"{voice}_fixed_cn_v1_16s.wav", "duration": 16.0},
                }
                for voice in voices
            ],
        }
        (catalog_dir / "voice_catalog_index.json").write_text(json.dumps(payload), encoding="utf-8")

    def test_missing_catalog_audio_blocks_as_required_system_asset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            catalog_dir = Path(tmp)
            voices = ["Achernar", "Achird", "Aoede"]
            self.write_catalog(catalog_dir, voices)

            with self.assertRaises(self.module.BlockedError) as ctx:
                self.module.load_catalog(catalog_dir, self.args())

            self.assertEqual(ctx.exception.code, "voice_catalog_audio_missing")
            self.assertIn("Required system voice catalog audio is missing", ctx.exception.message)

    def test_existing_catalog_audio_uses_full_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            catalog_dir = Path(tmp)
            voices = ["Achernar", "Achird", "Aoede", "Kore", "Sulafat"]
            self.write_catalog(catalog_dir, voices)
            for voice in voices:
                write_silent_wav(catalog_dir / f"{voice}_fixed_cn_v1_16s.wav")

            catalog = self.module.load_catalog(catalog_dir, self.args())

            self.assertEqual(len(catalog["voices"]), len(voices))
            self.assertNotIn("_missing_audio", catalog)
            self.assertNotIn("_bootstrap_voice_subset", catalog)

    def test_requested_voice_subset_still_requires_system_audio(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            catalog_dir = Path(tmp)
            voices = ["Achernar", "Aoede", "Kore", "Sulafat"]
            self.write_catalog(catalog_dir, voices)
            write_silent_wav(catalog_dir / "Achernar_fixed_cn_v1_16s.wav")

            with self.assertRaises(self.module.BlockedError) as ctx:
                self.module.load_catalog(catalog_dir, self.args(voices="Aoede,Kore,Sulafat"))

            self.assertEqual(ctx.exception.code, "voice_catalog_audio_missing")
            self.assertIn("Aoede_fixed_cn_v1_16s.wav", ctx.exception.message)

    def test_voice_catalog_wavs_are_not_gitignored(self) -> None:
        ignore_text = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")

        self.assertNotIn("ToolLibrary/Analysis_V1/VoiceCatalog/**/*.wav", ignore_text)

    def test_reference_pitch_harmonic_does_not_turn_male_voice_female(self) -> None:
        target = self.module.infer_target_gender({}, {
            "pitch_hz": 200.0,
            "pitch_confidence": 0.914,
            "pitch_method": "autocorrelation_median_f0",
            "spectral_peak_hz": 100.0,
        })

        self.assertEqual(target["target_gender"], "male")
        self.assertEqual(target["confidence"], "medium")
        self.assertEqual(target["source"], "reference_pitch_spectral_harmonic")

    def test_overlap_pitch_without_spectral_evidence_is_ambiguous(self) -> None:
        target = self.module.infer_target_gender({}, {
            "pitch_hz": 200.0,
            "pitch_confidence": 0.914,
            "pitch_method": "autocorrelation_median_f0",
            "spectral_peak_hz": 0.0,
        })

        self.assertEqual(target["target_gender"], "")
        self.assertEqual(target["confidence"], "low")
        self.assertEqual(target["source"], "reference_pitch_ambiguous")

    def test_clear_female_pitch_still_sets_female_gate(self) -> None:
        target = self.module.infer_target_gender({}, {
            "pitch_hz": 245.0,
            "pitch_confidence": 0.72,
            "pitch_method": "autocorrelation_median_f0",
            "spectral_peak_hz": 245.0,
        })

        self.assertEqual(target["target_gender"], "female")
        self.assertEqual(target["confidence"], "medium")
        self.assertEqual(target["source"], "reference_pitch")

    def test_post_json_retries_transient_http_errors(self) -> None:
        calls = []

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_: object) -> None:
                return None

            def read(self) -> bytes:
                return b'{"ok": true}'

        def fake_urlopen(request: object, timeout: int = 0):
            calls.append((request, timeout))
            if len(calls) == 1:
                raise self.module.urllib.error.HTTPError(
                    url="https://example.invalid",
                    code=503,
                    msg="unavailable",
                    hdrs=None,
                    fp=io.BytesIO(b'{"error":{"message":"The service is currently unavailable."}}'),
                )
            return FakeResponse()

        original_urlopen = self.module.urllib.request.urlopen
        original_sleep = self.module.time.sleep
        self.module.urllib.request.urlopen = fake_urlopen
        self.module.time.sleep = lambda _: None
        try:
            result = self.module.post_json("https://example.invalid", {"x": 1}, retry_delays=(0.0,))
        finally:
            self.module.urllib.request.urlopen = original_urlopen
            self.module.time.sleep = original_sleep

        self.assertEqual(result, {"ok": True})
        self.assertEqual(len(calls), 2)

    def test_post_json_does_not_retry_auth_errors(self) -> None:
        calls = []

        def fake_urlopen(request: object, timeout: int = 0):
            calls.append((request, timeout))
            raise self.module.urllib.error.HTTPError(
                url="https://example.invalid",
                code=401,
                msg="unauthorized",
                hdrs=None,
                fp=io.BytesIO(b'{"error":{"message":"API key is invalid."}}'),
            )

        original_urlopen = self.module.urllib.request.urlopen
        original_sleep = self.module.time.sleep
        self.module.urllib.request.urlopen = fake_urlopen
        self.module.time.sleep = lambda _: None
        try:
            with self.assertRaises(self.module.ToolError) as ctx:
                self.module.post_json("https://example.invalid", {"x": 1}, retry_delays=(0.0, 0.0))
        finally:
            self.module.urllib.request.urlopen = original_urlopen
            self.module.time.sleep = original_sleep

        self.assertIn("HTTP 401", str(ctx.exception))
        self.assertEqual(len(calls), 1)

    def test_post_json_does_not_retry_resource_exhausted_429(self) -> None:
        calls = []

        def fake_urlopen(request: object, timeout: int = 0):
            calls.append((request, timeout))
            raise self.module.urllib.error.HTTPError(
                url="https://example.invalid",
                code=429,
                msg="resource exhausted",
                hdrs=None,
                fp=io.BytesIO(b'{"error":{"code":429,"message":"Your prepayment credits are depleted.","status":"RESOURCE_EXHAUSTED"}}'),
            )

        original_urlopen = self.module.urllib.request.urlopen
        original_sleep = self.module.time.sleep
        self.module.urllib.request.urlopen = fake_urlopen
        self.module.time.sleep = lambda _: None
        try:
            with self.assertRaises(self.module.ToolError) as ctx:
                self.module.post_json("https://example.invalid", {"x": 1}, retry_delays=(0.0, 0.0, 0.0))
        finally:
            self.module.urllib.request.urlopen = original_urlopen
            self.module.time.sleep = original_sleep

        self.assertIn("HTTP 429", str(ctx.exception))
        self.assertNotIn("after 4 attempts", str(ctx.exception))
        self.assertEqual(len(calls), 1)

    def test_no_audio_then_invalid_argument_retry_is_audited_as_candidate_local(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            prompt_path = workspace / "prompt.txt"
            output_path = workspace / "output.wav"
            prompt_path.write_text("正文：你好", encoding="utf-8")
            responses = [
                {"candidates": [{"finishReason": "OTHER"}]},
                self.module.ToolError(
                    'HTTP 400: {"error":{"code":400,"message":"Request contains an invalid argument.","status":"INVALID_ARGUMENT"}}'
                ),
            ]
            audits = []

            def fake_post_json(*_: object, **__: object):
                response = responses.pop(0)
                if isinstance(response, Exception):
                    raise response
                return response

            def fake_record_tts_audit(**kwargs: object) -> None:
                audits.append(kwargs)

            original_post_json = self.module.post_json
            original_record_tts_audit = self.module.record_tts_audit
            self.module.post_json = fake_post_json
            self.module.record_tts_audit = fake_record_tts_audit
            try:
                with self.assertRaises(self.module.ToolError) as ctx:
                    self.module.call_gemini_tts(
                        "test-key",
                        self.module.DEFAULT_TTS_MODEL,
                        "Gacrux",
                        prompt_path,
                        output_path,
                        workspace=workspace,
                        asset_key="candidate_002_Gacrux",
                    )
            finally:
                self.module.post_json = original_post_json
                self.module.record_tts_audit = original_record_tts_audit

            self.assertTrue(self.module.is_candidate_local_tts_error(ctx.exception))
            self.assertEqual(
                [audit["error_code"] for audit in audits],
                ["primary_response_without_audio", "retry_request_failed"],
            )
            self.assertEqual(audits[-1]["voice"], "Gacrux")
            self.assertEqual(audits[-1]["response"]["retry_reason"], "OTHER")

    def test_candidate_local_failure_uses_next_ranked_voice(self) -> None:
        rows = [
            {"voice": voice, "rank": rank, "provider": "google", "model": self.module.DEFAULT_TTS_MODEL}
            for rank, voice in enumerate(["Laomedeia", "Gacrux", "Orus", "Puck", "Fenrir"], 1)
        ]
        calls = []

        def fake_generate(
            workspace: Path,
            args: object,
            api_key: str,
            model: str,
            scene_profile: dict,
            reference_text: str,
            row: dict,
            rank: int,
            target_duration: float,
        ):
            calls.append((row["voice"], rank))
            if row["voice"] == "Gacrux":
                raise self.module.ToolError(
                    'HTTP 400: {"error":{"message":"Request contains an invalid argument.","status":"INVALID_ARGUMENT"}}'
                )
            return {**row, "output_rank": rank}, 1

        original_generate = self.module.generate_model_candidate
        self.module.generate_model_candidate = fake_generate
        result = {"warnings": [], "counts": {}}
        try:
            generated, model_calls, attempts, failures = self.module.generate_ranked_model_candidates(
                Path("/tmp/workspace"),
                self.args(final_count=3),
                "test-key",
                self.module.DEFAULT_TTS_MODEL,
                {},
                "你好",
                rows,
                16.0,
                result,
            )
        finally:
            self.module.generate_model_candidate = original_generate

        self.assertEqual([row["voice"] for row in generated], ["Laomedeia", "Orus", "Puck"])
        self.assertEqual([row["output_rank"] for row in generated], [1, 2, 3])
        self.assertEqual([row["generation_source_rank"] for row in generated], [1, 3, 4])
        self.assertEqual(calls, [("Laomedeia", 1), ("Gacrux", 2), ("Orus", 2), ("Puck", 3)])
        self.assertEqual((model_calls, attempts, failures), (3, 4, 1))
        self.assertEqual(result["warnings"][0]["code"], "tts_candidate_generation_failed")
        self.assertEqual(result["warnings"][0]["voice"], "Gacrux")

    def test_global_provider_error_stops_candidate_fallback(self) -> None:
        rows = [
            {"voice": voice, "rank": rank, "provider": "google", "model": self.module.DEFAULT_TTS_MODEL}
            for rank, voice in enumerate(["Laomedeia", "Gacrux", "Orus", "Puck"], 1)
        ]
        for error_message in (
            'HTTP 401: {"error":{"message":"API key is invalid."}}',
            'HTTP 403: {"error":{"message":"Permission denied."}}',
            'HTTP 429: {"error":{"status":"RESOURCE_EXHAUSTED"}}',
            'HTTP 503 after 4 attempts: {"error":{"message":"Service unavailable."}}',
            'Network error after 4 attempts: timed out',
        ):
            with self.subTest(error_message=error_message):
                calls = []

                def fake_generate(*args: object):
                    row = args[6]
                    calls.append(row["voice"])
                    raise self.module.ToolError(error_message)

                original_generate = self.module.generate_model_candidate
                self.module.generate_model_candidate = fake_generate
                try:
                    with self.assertRaises(self.module.ToolError) as ctx:
                        self.module.generate_ranked_model_candidates(
                            Path("/tmp/workspace"),
                            self.args(final_count=3),
                            "test-key",
                            self.module.DEFAULT_TTS_MODEL,
                            {},
                            "你好",
                            rows,
                            16.0,
                            {"warnings": [], "counts": {}},
                        )
                finally:
                    self.module.generate_model_candidate = original_generate

                self.assertEqual(str(ctx.exception), error_message)
                self.assertEqual(calls, ["Laomedeia"])

    def test_candidate_fallback_is_bounded_and_reports_pool_exhaustion(self) -> None:
        rows = [
            {"voice": f"Voice{rank}", "rank": rank, "provider": "google", "model": self.module.DEFAULT_TTS_MODEL}
            for rank in range(1, 11)
        ]
        calls = []

        def fake_generate(*args: object):
            row = args[6]
            calls.append(row["voice"])
            raise self.module.ToolError(
                'HTTP 400: {"error":{"message":"Request contains an invalid argument.","status":"INVALID_ARGUMENT"}}'
            )

        original_generate = self.module.generate_model_candidate
        self.module.generate_model_candidate = fake_generate
        result = {"warnings": [], "counts": {}}
        try:
            with self.assertRaises(self.module.CandidatePoolExhaustedError) as ctx:
                self.module.generate_ranked_model_candidates(
                    Path("/tmp/workspace"),
                    self.args(final_count=3),
                    "test-key",
                    self.module.DEFAULT_TTS_MODEL,
                    {},
                    "你好",
                    rows,
                    16.0,
                    result,
                )
        finally:
            self.module.generate_model_candidate = original_generate

        self.assertEqual(len(calls), 6)
        self.assertEqual(ctx.exception.attempted, 6)
        self.assertEqual(result["counts"]["candidate_pool_limit"], 6)
        self.assertEqual(len(result["warnings"]), 6)
        self.assertEqual(self.module.classify_failure(ctx.exception)[0], "tts_candidate_pool_exhausted")

    def test_failed_forced_rerun_restores_previous_tts_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / "SessionContext").mkdir(parents=True)
            (workspace / "SessionOutput" / "subtitle").mkdir(parents=True)
            (workspace / "SessionOutput" / "tts").mkdir(parents=True)
            (workspace / "SessionContext" / "Variables.json").write_text("{}", encoding="utf-8")
            (workspace / "SessionOutput" / "Audio_Reference.wav").write_bytes(b"reference")
            (workspace / "SessionOutput" / "subtitle" / "final_srt_frame_items.json").write_text(
                json.dumps({"items": [{"id": "1", "dialogue": "你好"}]}, ensure_ascii=False),
                encoding="utf-8",
            )
            final_path = workspace / self.module.SESSION_TTS_FINAL_REL
            candidate_path = workspace / self.module.session_candidate_path(1)
            final_path.write_bytes(b"previous-final")
            candidate_path.write_bytes(b"previous-audio")

            def fake_run_builder(*_: object) -> None:
                raise self.module.CandidatePoolExhaustedError(generated=1, required=3, attempted=6, failures=5)

            original_run_builder = self.module.run_builder
            self.module.run_builder = fake_run_builder
            try:
                result = self.module.run(self.args(workspace=str(workspace), force=True))
            finally:
                self.module.run_builder = original_run_builder

            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["error"]["code"], "tts_candidate_pool_exhausted")
            self.assertEqual(final_path.read_bytes(), b"previous-final")
            self.assertEqual(candidate_path.read_bytes(), b"previous-audio")
            self.assertIn(
                "restored_previous_tts_outputs_after_failed_force_rerun",
                [warning["code"] for warning in result["warnings"]],
            )

    def test_run_promotes_provider_quota_error_to_top_level_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / "SessionContext").mkdir(parents=True)
            (workspace / "SessionOutput" / "subtitle").mkdir(parents=True)
            (workspace / "SessionOutput" / "Audio_Reference.wav").write_bytes(b"fake")
            (workspace / "SessionContext" / "Variables.json").write_text("{}", encoding="utf-8")
            (workspace / "SessionOutput" / "subtitle" / "final_srt_frame_items.json").write_text(
                json.dumps({"items": [{"id": "1", "dialogue": "你好"}]}, ensure_ascii=False),
                encoding="utf-8",
            )

            def fake_run_builder(*_: object) -> None:
                raise self.module.ToolError(
                    'HTTP 429: {"error":{"code":429,"message":"Your prepayment credits are depleted.","status":"RESOURCE_EXHAUSTED"}}'
                )

            original_run_builder = self.module.run_builder
            self.module.run_builder = fake_run_builder
            try:
                result = self.module.run(self.args(workspace=str(workspace)))
            finally:
                self.module.run_builder = original_run_builder

            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["error"]["code"], "provider_resource_exhausted")
            self.assertIn("prepayment credits are depleted", result["error"]["message"])
            self.assertIn({"code": "provider_resource_exhausted", "message": result["error"]["message"]}, result["warnings"])
            report = json.loads((workspace / self.module.REPORT_RESULT_REL).read_text(encoding="utf-8"))
            self.assertEqual(report["error"]["code"], "provider_resource_exhausted")


if __name__ == "__main__":
    unittest.main()
