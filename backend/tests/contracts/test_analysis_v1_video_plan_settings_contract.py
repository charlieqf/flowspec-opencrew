from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class AnalysisV1VideoPlanSettingsContractTest(unittest.TestCase):
    def test_split_tolerance_changes_dialogue_range_grouping_without_crossing_grok_cap(self) -> None:
        planner = load_module(
            REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "05_01_VideoPlanGenerator.py",
            "analysis_v1_05_01_video_plan_settings_contract",
        )
        dialogues = [
            {"start": 0.0, "end": 5.0, "duration": 5.0},
            {"start": 5.0, "end": 9.0, "duration": 4.0},
            {"start": 9.0, "end": 13.0, "duration": 4.0},
        ]

        strict_ranges = planner.segment_ranges_for_dialogues(dialogues, 0, 2, 8.0, 0.0)
        tolerant_ranges = planner.segment_ranges_for_dialogues(dialogues, 0, 2, 8.0, 2.0)

        self.assertEqual(strict_ranges, [(0, 0, False), (1, 2, False)])
        self.assertEqual(tolerant_ranges, [(0, 1, False), (2, 2, False)])

        grok_cap_ranges = planner.segment_ranges_for_dialogues(
            [{"start": 0.0, "end": 8.0, "duration": 8.0}, {"start": 8.0, "end": 16.0, "duration": 8.0}],
            0,
            1,
            15.0,
            5.0,
        )
        self.assertEqual(grok_cap_ranges, [(0, 0, False), (1, 1, False)])

    def test_xai_video_duration_is_capped_at_grok_limit(self) -> None:
        video_grok = load_module(
            REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "video_plan_executor_modules" / "video_grok.py",
            "analysis_v1_video_grok_seconds_contract",
        )

        self.assertEqual(video_grok.provider_video_seconds({"provider": "xai"}, 22.0), 15)

    def test_xai_quality_model_defaults_to_1080p_without_overriding_explicit_resolution(self) -> None:
        video_grok = load_module(
            REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "video_plan_executor_modules" / "video_grok.py",
            "analysis_v1_video_grok_resolution_contract",
        )

        quality_model = "grok-imagine-video-1.5-preview"
        self.assertEqual(video_grok.normalize_video_resolution("", quality_model), "1080p")
        self.assertEqual(video_grok.normalize_video_resolution("720p", quality_model), "720p")
        self.assertEqual(video_grok.normalize_video_resolution("", "grok-imagine-video"), "720p")
        self.assertEqual(video_grok.normalize_video_resolution("1080p", "grok-imagine-video"), "720p")

    def test_kling_omni_duration_rounds_segment_duration_within_supported_range(self) -> None:
        executor = load_module(
            REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "05_02_VideoPlanExecutor.py",
            "analysis_v1_05_02_kling_omni_seconds_contract",
        )
        video_kling = load_module(
            REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "video_plan_executor_modules" / "video_kling.py",
            "analysis_v1_video_kling_seconds_contract",
        )

        config = {"provider": "kling", "model": "kling-v3-omni"}
        self.assertEqual(executor.provider_video_seconds(config, 2.2), 3)
        self.assertEqual(executor.provider_video_seconds(config, 8.4), 8)
        self.assertEqual(executor.provider_video_seconds(config, 8.6), 9)
        self.assertEqual(executor.provider_video_seconds(config, 18.0), 15)
        self.assertEqual(video_kling.provider_video_seconds(8.6, "kling-v3-omni"), 9)

    def test_chanjing_kling_dispatches_to_chanjing_module(self) -> None:
        executor = load_module(
            REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "05_02_VideoPlanExecutor.py",
            "analysis_v1_05_02_chanjing_kling_dispatch_contract",
        )

        module = executor.video_module_for("chanjing", "kling2.5")

        self.assertEqual(module.TEMPLATE_NAME, "Ref_05_02_Video_ChanJing_Kling.md")

    def test_chanjing_video_models_dispatch_to_separate_modules(self) -> None:
        executor = load_module(
            REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "05_02_VideoPlanExecutor.py",
            "analysis_v1_05_02_chanjing_video_dispatch_contract",
        )

        cases = {
            "viduq1": "Ref_05_02_Video_ChanJing_ViduQ1.md",
            "MiniMax-Hailuo-02": "Ref_05_02_Video_ChanJing_Hailuo02.md",
            "Doubao-Seedance-1.0-pro": "Ref_05_02_Video_ChanJing_Doubao.md",
            "Doubao-Seedance-1.0-lite-i2v": "Ref_05_02_Video_ChanJing_Doubao.md",
            "happyhorse-1.0-i2v": "Ref_05_02_Video_ChanJing_HappyHorse.md",
            "happyhorse-1.0-r2v": "Ref_05_02_Video_ChanJing_HappyHorse.md",
        }

        for model, template_name in cases.items():
            with self.subTest(model=model):
                module = executor.video_module_for("chanjing", model)
                self.assertEqual(module.TEMPLATE_NAME, template_name)

    def test_chanjing_kling_duration_rounds_to_openapi_enum(self) -> None:
        executor = load_module(
            REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "05_02_VideoPlanExecutor.py",
            "analysis_v1_05_02_chanjing_kling_seconds_contract",
        )
        module = load_module(
            REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "video_plan_executor_modules" / "video_chanjing_kling.py",
            "analysis_v1_video_chanjing_kling_seconds_contract",
        )

        config = {"provider": "chanjing", "model": "kling2.5"}
        self.assertEqual(executor.provider_video_seconds(config, 2.2), 5)
        self.assertEqual(executor.provider_video_seconds(config, 5.6), 6)
        self.assertEqual(executor.provider_video_seconds(config, 8.4), 10)
        self.assertEqual(executor.provider_video_seconds(config, 18.0), 10)
        self.assertEqual(module.provider_video_seconds(config, 5.6), 6)

    def test_chanjing_kling_payload_uses_openapi_fields(self) -> None:
        module = load_module(
            REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "video_plan_executor_modules" / "video_chanjing_kling.py",
            "analysis_v1_video_chanjing_kling_payload_contract",
        )

        payload = module.request_payload(
            "relaxed presenter with natural hand gestures",
            "kling2.5",
            {"full_path": "https://res.chanjing.example/first-frame.jpg"},
            10,
            {"aspect_ratio": "9:16", "clarity": 1080, "quality_mode": "pro"},
        )

        self.assertEqual(payload["start_frame"], "https://res.chanjing.example/first-frame.jpg")
        self.assertEqual(payload["ref_prompt"], "relaxed presenter with natural hand gestures")
        self.assertEqual(payload["creation_type"], 4)
        self.assertEqual(payload["model_code"], "kling2.5")
        self.assertEqual(payload["aspect_ratio"], "9:16")
        self.assertEqual(payload["clarity"], 1080)
        self.assertEqual(payload["quality_mode"], "pro")
        self.assertEqual(payload["video_duration"], 10)
        for key in ("sound", "generate_audio", "mute", "muted", "no_audio", "without_audio"):
            self.assertNotIn(key, payload)

    def test_chanjing_non_kling_payload_uses_openapi_fields(self) -> None:
        module = load_module(
            REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "video_plan_executor_modules" / "video_chanjing_hailuo02.py",
            "analysis_v1_video_chanjing_hailuo02_payload_contract",
        )

        payload = module.request_payload(
            "relaxed presenter with natural hand gestures",
            "MiniMax-Hailuo-02",
            {"full_path": "https://res.chanjing.example/first-frame.jpg"},
            6,
            {"aspect_ratio": "9:16", "clarity": 1080, "quality_mode": "pro"},
        )

        self.assertEqual(payload["start_frame"], "https://res.chanjing.example/first-frame.jpg")
        self.assertEqual(payload["ref_prompt"], "relaxed presenter with natural hand gestures")
        self.assertEqual(payload["creation_type"], 4)
        self.assertEqual(payload["model_code"], "MiniMax-Hailuo-02")
        self.assertNotIn("aspect_ratio", payload)
        self.assertNotIn("quality_mode", payload)
        self.assertEqual(payload["clarity"], 1080)
        self.assertEqual(payload["video_duration"], 6)

    def test_happyhorse_r2v_payload_uses_extra_reference_images(self) -> None:
        module = load_module(
            REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "video_plan_executor_modules" / "video_chanjing_happyhorse.py",
            "analysis_v1_video_chanjing_happyhorse_r2v_payload_contract",
        )

        payload = module.request_payload(
            "keep the same host and product identity",
            "happyhorse-1.0-r2v",
            {"full_path": "https://res.chanjing.example/start-frame.jpg"},
            6,
            {"aspect_ratio": "9:16", "clarity": 1080, "quality_mode": "pro"},
            [
                {"full_path": "https://res.chanjing.example/person-ref.jpg"},
                {"full_path": "https://res.chanjing.example/product-ref.jpg"},
            ],
        )

        self.assertEqual(payload["start_frame"], "https://res.chanjing.example/start-frame.jpg")
        self.assertEqual(payload["ref_img_url"], [
            "https://res.chanjing.example/person-ref.jpg",
            "https://res.chanjing.example/product-ref.jpg",
        ])

    def test_wan_rtv_resolves_explicit_reference_video_before_bundled_sample(self) -> None:
        module = load_module(
            REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "video_plan_executor_modules" / "video_wan_rtv.py",
            "analysis_v1_video_wan_rtv_reference_contract",
        )

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            prompt_path = tmp_path / "Prompt" / "video.json"
            prompt_path.parent.mkdir(parents=True)
            reference_video = tmp_path / "custom_reference.mp4"
            reference_video.write_bytes(b"fake-video")

            resolved = module.resolve_reference_video({"reference_videos": [str(reference_video)]}, prompt_path)

        self.assertEqual(resolved, reference_video)

    def test_chanjing_non_kling_templates_render_required_blocks(self) -> None:
        cases = {
            "video_chanjing_viduq1.py": "video_chanjing_viduq1",
            "video_chanjing_hailuo02.py": "video_chanjing_hailuo02",
            "video_chanjing_doubao.py": "video_chanjing_doubao",
            "video_chanjing_happyhorse.py": "video_chanjing_happyhorse",
        }
        context = {
            "segment": {
                "segment_id": "segment_001",
                "planned_video_duration": 6,
                "dialogue_ids": ["d1"],
                "tasks": {"need_lipsync": True},
            },
            "dialogue_index": {"d1": {"dialogue": {"text": "这是一段自然口播测试。"}}},
        }

        for filename, expected_profile in cases.items():
            with self.subTest(filename=filename):
                module = load_module(
                    REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "video_plan_executor_modules" / filename,
                    f"analysis_v1_{expected_profile}_template_contract",
                )
                package = module.build_prompt_package(context)
                self.assertEqual(package["provider_profile"], expected_profile)
                self.assertIn("这是一段自然口播测试。", package["prompt"])
                self.assertIn("Negative prompt:", package["prompt"])
                self.assertNotIn("VIDEO_CHANJING_KLING", "\n".join(package["template_blocks"]))

    def test_video_config_normalizes_stale_saved_silent_defaults(self) -> None:
        executor = load_module(
            REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "05_02_VideoPlanExecutor.py",
            "analysis_v1_05_02_video_audio_defaults_contract",
        )

        self.assertEqual(
            executor.normalize_video_audio_defaults("video", "kling", "kling-3.0-turbo", {"sound": "off"})["sound"],
            "on",
        )
        self.assertEqual(
            executor.normalize_video_audio_defaults("video", "chanjing", "kling2.5", {"sound": "off"})["sound"],
            "on",
        )
        self.assertTrue(
            executor.normalize_video_audio_defaults("video", "bytedance", "doubao-seedance-2-0-fast-260128", {"generate_audio": False})["generate_audio"],
        )

    def test_chanjing_kling_query_path_uses_task_endpoint(self) -> None:
        module = load_module(
            REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "video_plan_executor_modules" / "video_chanjing_kling.py",
            "analysis_v1_video_chanjing_kling_query_contract",
        )

        self.assertEqual(
            module.query_task_url({"base_url": "https://open-api.chanjing.cc", "query_path": "/open/v1/ai_creation/task/info"}, "task_123"),
            "https://open-api.chanjing.cc/open/v1/ai_creation/task?unique_id=task_123",
        )

    def test_kling_tmpfiles_url_is_converted_to_direct_download_url(self) -> None:
        video_kling = load_module(
            REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "video_plan_executor_modules" / "video_kling.py",
            "analysis_v1_video_kling_tmpfiles_contract",
        )

        self.assertEqual(
            video_kling.tmpfiles_direct_url("https://tmpfiles.org/abc123/reference_video_10s.mp4"),
            "https://tmpfiles.org/dl/abc123/reference_video_10s.mp4",
        )

    def test_kling_tmpfiles_upload_sends_only_file_field(self) -> None:
        video_kling = load_module(
            REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "video_plan_executor_modules" / "video_kling.py",
            "analysis_v1_video_kling_tmpfiles_upload_contract",
        )
        calls = []

        def fake_post_multipart_request(url, fields, files, headers=None, timeout=180):
            calls.append({"url": url, "fields": fields, "files": files, "headers": headers, "timeout": timeout})
            return {"status": "success", "data": {"url": "https://tmpfiles.org/abc123/reference_video_10s.mp4"}}

        original = video_kling.post_multipart_request
        video_kling.post_multipart_request = fake_post_multipart_request
        try:
            with tempfile.TemporaryDirectory() as tmp:
                video_path = Path(tmp) / "reference_video_10s.mp4"
                video_path.write_bytes(b"fake-video")
                url = video_kling.tmpfiles_upload_video(video_path, {"tmpfiles_expire_seconds": 21600})
        finally:
            video_kling.post_multipart_request = original

        self.assertEqual(url, "https://tmpfiles.org/dl/abc123/reference_video_10s.mp4")
        self.assertEqual(calls[0]["fields"], {})
        self.assertEqual(calls[0]["files"][0][0], "file")

    def test_kling_imports_safe_provider_downloader(self) -> None:
        video_kling = load_module(
            REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "video_plan_executor_modules" / "video_kling.py",
            "analysis_v1_video_kling_downloader_contract",
        )

        self.assertIsNotNone(video_kling.safe_download_to_path)


if __name__ == "__main__":
    unittest.main()
