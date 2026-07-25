from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[3]
for path in (REPO_ROOT / "backend",):
    value = str(path)
    if value not in sys.path:
        sys.path.insert(0, value)

ROUTES_PATH = REPO_ROOT / "backend" / "opcrew_backend" / "koubo" / "koubo_storyboard" / "asset_digital_human_routes.py"
KOUBO_API_PATH = REPO_ROOT / "frontend" / "src" / "modules" / "koubo" / "KouboStoryBoard" / "kouboStoryboardApi.js"
DIGITAL_HUMAN_OVERLAY_PATH = REPO_ROOT / "frontend" / "src" / "modules" / "koubo" / "UploadAssetLibrary" / "UploadAssetLibraryOverlay.jsx"
DIGITAL_HUMAN_PANEL_PATH = REPO_ROOT / "frontend" / "src" / "modules" / "koubo" / "UploadAssetLibrary" / "digitalHuman" / "DigitalHumanAgentPanel.jsx"
DIGITAL_HUMAN_AVATAR_MODAL_PATH = REPO_ROOT / "frontend" / "src" / "modules" / "koubo" / "UploadAssetLibrary" / "digitalHuman" / "AvatarPickerModal.jsx"
DIGITAL_HUMAN_CSS_PATH = REPO_ROOT / "frontend" / "src" / "modules" / "koubo" / "UploadAssetLibrary" / "digitalHuman" / "digitalHuman.css"


class KouboAssetDigitalHumanContractTest(unittest.TestCase):
    def test_video_agent_create_request_uses_chat_mode_without_auto_proceed(self) -> None:
        from opcrew_backend.koubo.koubo_storyboard import asset_digital_human_services

        calls: list[dict[str, object]] = []

        def fake_json_request(_api_key: str, method: str, path: str, payload: dict[str, object] | None = None, **_kwargs):
            calls.append({"method": method, "path": path, "payload": dict(payload or {})})
            if method == "POST" and path == "/v3/video-agents":
                return {"data": {"session_id": "session_123"}}
            return {}

        task = {"id": 5, "session_id": 6}
        payload = {
            "prompt": "请生成一条中文口播数字人视频。",
            "aspect": "9:16",
            "avatar_id": "avatar_123",
            "voice_id": "voice_123",
            "agent_confirm_generate": False,
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            with (
                patch.object(asset_digital_human_services, "_heygen_key", lambda _ctx: "test-key"),
                patch.object(asset_digital_human_services, "_json_request", fake_json_request),
                patch.object(asset_digital_human_services, "_poll_video_agent_review", lambda _api_key, _session_id: {"status": "reviewing", "messages": []}),
            ):
                result = asset_digital_human_services.start_video_agent_chat_plan(SimpleNamespace(), Path(tmpdir), task, payload)

        self.assertTrue(result["ok"])
        create_payload = next(call["payload"] for call in calls if call["path"] == "/v3/video-agents")
        self.assertNotIn("auto_proceed", create_payload)
        self.assertEqual(create_payload["prompt"], f"不要生成视频，只修改计划\n{payload['prompt']}")
        self.assertEqual(create_payload["mode"], "chat")
        self.assertEqual(create_payload["avatar_id"], "avatar_123")
        self.assertEqual(create_payload["voice_id"], "voice_123")
        self.assertEqual(create_payload["orientation"], "portrait")

    def test_video_agent_plan_resources_are_synced_from_message_resource_ids(self) -> None:
        from opcrew_backend.koubo.koubo_storyboard import asset_digital_human_services

        calls: list[dict[str, object]] = []

        def fake_json_request(_api_key: str, method: str, path: str, payload: dict[str, object] | None = None, **_kwargs):
            calls.append({"method": method, "path": path, "payload": dict(payload or {})})
            if method == "POST" and path == "/v3/video-agents":
                return {"data": {"session_id": "session_123"}}
            if method == "GET" and path == "/v3/video-agents/session_123/resources/res_storyboard_001":
                return {"data": {
                    "resource_id": "res_storyboard_001",
                    "resource_type": "draft",
                    "source_type": "generated",
                    "url": "https://files.heygen.ai/resources/res_storyboard_001.json",
                    "metadata": {"title": "远离情绪内耗", "scenes": [{"voiceover": "开篇"}]},
                }}
            return {}

        session_payload = {
            "session_id": "session_123",
            "status": "reviewing",
            "progress": 45,
            "title": "远离情绪内耗",
            "messages": [
                {
                    "role": "model",
                    "content": "Storyboard is ready for review.",
                    "resource_ids": ["res_storyboard_001"],
                },
                {"role": "user", "content": "请生成一条中文口播视频。"},
            ],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            with (
                patch.object(asset_digital_human_services, "_heygen_key", lambda _ctx: "test-key"),
                patch.object(asset_digital_human_services, "_json_request", fake_json_request),
                patch.object(asset_digital_human_services, "_poll_video_agent_review", lambda _api_key, _session_id: session_payload),
            ):
                result = asset_digital_human_services.start_video_agent_chat_plan(SimpleNamespace(), Path(tmpdir), {"id": 5, "session_id": 6}, {
                    "prompt": "请生成一条中文口播视频。",
                    "aspect": "9:16",
                })

        self.assertEqual(result["agent_status"], "reviewing")
        self.assertEqual(result["agent_title"], "远离情绪内耗")
        self.assertEqual(result["plan_text"], "Storyboard is ready for review.")
        self.assertEqual(result["agent_resources"][0]["resource_id"], "res_storyboard_001")
        self.assertEqual(result["agent_resources"][0]["request_resource_id"], "res_storyboard_001")
        self.assertEqual(result["agent_resources"][0]["metadata"]["title"], "远离情绪内耗")
        self.assertIn("/v3/video-agents/session_123/resources/res_storyboard_001", [call["path"] for call in calls])

    def test_video_agent_session_sync_route_is_wired(self) -> None:
        source = ROUTES_PATH.read_text(encoding="utf-8")

        for token in (
            "sync_video_agent_chat_plan",
            "stop_video_agent_chat_plan",
            "/asset-library/digital-human/agents/{provider_session_id}",
            "/asset-library/digital-human/agents/{provider_session_id}/stop",
            "koubo_storyboard.asset_library.digital_human.agent.synced",
            "koubo_storyboard.asset_library.digital_human.agent.stopped",
        ):
            self.assertIn(token, source)

    def test_video_agent_stop_calls_provider_stop_endpoint(self) -> None:
        from opcrew_backend.koubo.koubo_storyboard import asset_digital_human_services

        calls: list[dict[str, object]] = []

        def fake_json_request(_api_key: str, method: str, path: str, payload: dict[str, object] | None = None, **_kwargs):
            calls.append({"method": method, "path": path, "payload": dict(payload or {})})
            if method == "POST" and path == "/v3/video-agents/session_123/stop":
                return {"data": {"ok": True}}
            if method == "GET" and path == "/v3/video-agents/session_123":
                return {"data": {"session_id": "session_123", "status": "waiting_for_input", "messages": []}}
            return {}

        with tempfile.TemporaryDirectory() as tmpdir:
            with (
                patch.object(asset_digital_human_services, "_heygen_key", lambda _ctx: "test-key"),
                patch.object(asset_digital_human_services, "_json_request", fake_json_request),
            ):
                result = asset_digital_human_services.stop_video_agent_chat_plan(SimpleNamespace(), Path(tmpdir), {"id": 5, "session_id": 6}, "session_123")

        self.assertTrue(result["ok"])
        self.assertEqual(result["agent_status"], "waiting_for_input")
        stop_call = calls[0]
        self.assertEqual(stop_call["method"], "POST")
        self.assertEqual(stop_call["path"], "/v3/video-agents/session_123/stop")

    def test_video_agent_sync_downloads_completed_video_to_asset_library(self) -> None:
        from opcrew_backend.koubo.koubo_storyboard import asset_digital_human_services

        calls: list[dict[str, object]] = []

        def fake_json_request(_api_key: str, method: str, path: str, payload: dict[str, object] | None = None, **_kwargs):
            calls.append({"method": method, "path": path, "payload": dict(payload or {})})
            if method == "GET" and path == "/v3/video-agents/session_123":
                return {"data": {"session_id": "session_123", "status": "completed", "video_id": "video_123", "title": "完成的视频", "messages": []}}
            return {}

        def fake_poll_video(_api_key: str, video_id: str):
            self.assertEqual(video_id, "video_123")
            return {"id": "video_123", "status": "completed", "video_url": "https://files.heygen.ai/video/video_123.mp4", "duration": 8}

        def fake_download(_url: str, output_path: Path) -> None:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"video")

        with tempfile.TemporaryDirectory() as tmpdir:
            with (
                patch.object(asset_digital_human_services, "_heygen_key", lambda _ctx: "test-key"),
                patch.object(asset_digital_human_services, "_json_request", fake_json_request),
                patch.object(asset_digital_human_services, "_poll_video", fake_poll_video),
                patch.object(asset_digital_human_services, "_download_to_path", fake_download),
            ):
                result = asset_digital_human_services.sync_video_agent_chat_plan(SimpleNamespace(), Path(tmpdir), {"id": 5, "session_id": 6}, "session_123")

            self.assertEqual(result["generated_count"], 1)
            self.assertEqual(result["asset"]["source"], "digital_human_agent")
            self.assertEqual(result["asset"]["label"], "Digital human agent video")
            public_asset_fields = {key: result["asset"][key] for key in ("id", "path", "label", "filename", "source")}
            self.assertNotIn("heygen", json.dumps(public_asset_fields, ensure_ascii=False).lower())
            self.assertTrue((Path(tmpdir) / result["asset"]["path"]).is_file())
            from opcrew_backend.model_leakage_guard import sanitize_customer_payload
            from opcrew_backend.routes.auth import AUTH_ROLE_USER

            sanitized = sanitize_customer_payload(SimpleNamespace(), AUTH_ROLE_USER, result)
            sanitized_path = sanitized["asset"]["path"]
            self.assertEqual(sanitized_path, result["asset"]["path"])
            self.assertNotIn("[model]", sanitized_path)
            self.assertNotIn("heygen", sanitized_path.lower())
            self.assertTrue((Path(tmpdir) / sanitized_path).is_file())
            manifest = asset_digital_human_services._read_json(Path(tmpdir) / "SessionOutput/storyboard/koubo_storyboard_assets.json")
            self.assertEqual(manifest["assets"][0]["origin"]["provider_video_id"], "video_123")

        self.assertIn("/v3/video-agents/session_123", [call["path"] for call in calls])

    def test_video_agent_sync_can_skip_completed_video_materialization_for_resume(self) -> None:
        from opcrew_backend.koubo.koubo_storyboard import asset_digital_human_services

        def fail_download(*_args, **_kwargs):
            raise AssertionError("resume sync must not download completed video")

        session_payload = {"session_id": "session_123", "status": "completed", "video_id": "video_123", "messages": []}

        with tempfile.TemporaryDirectory() as tmpdir:
            with (
                patch.object(asset_digital_human_services, "_heygen_key", lambda _ctx: "test-key"),
                patch.object(asset_digital_human_services, "_json_request", lambda *_args, **_kwargs: {"data": session_payload}),
                patch.object(asset_digital_human_services, "_download_to_path", fail_download),
            ):
                result = asset_digital_human_services.sync_video_agent_chat_plan(SimpleNamespace(), Path(tmpdir), {"id": 5, "session_id": 6}, "session_123", materialize_completed=False)

            self.assertEqual(result["agent_status"], "completed")
            self.assertEqual(result["provider_video_id"], "video_123")
            self.assertEqual(result["generated_count"], 0)
            self.assertEqual(result["assets"], [])

    def test_video_agent_final_provider_result_keeps_all_video_api_fields(self) -> None:
        from opcrew_backend.koubo.koubo_storyboard import asset_digital_human_services

        calls: list[dict[str, object]] = []

        def fake_json_request(_api_key: str, method: str, path: str, payload: dict[str, object] | None = None, **_kwargs):
            calls.append({"method": method, "path": path, "payload": dict(payload or {})})
            if method == "POST" and path == "/v3/video-agents/session_123":
                return {"data": {"session_id": "session_123", "run_id": "run_123"}}
            return {}

        def fake_download(_url: str, output_path: Path) -> None:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"video")

        video_payload = {
            "id": "video_123",
            "status": "completed",
            "video_url": "https://files.heygen.ai/video/video_123.mp4",
            "thumbnail_url": "https://files.heygen.ai/thumb/video_123.jpg",
            "gif_url": "https://files.heygen.ai/gif/video_123.gif",
            "captioned_video_url": "https://files.heygen.ai/video/video_123_captioned.mp4",
            "subtitle_url": "https://files.heygen.ai/srt/video_123.srt",
            "duration": 12.5,
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            with (
                patch.object(asset_digital_human_services, "_heygen_key", lambda _ctx: "test-key"),
                patch.object(asset_digital_human_services, "_json_request", fake_json_request),
                patch.object(asset_digital_human_services, "_poll_video_agent", lambda _api_key, _session_id: ("video_123", {"session_id": "session_123", "status": "generating"})),
                patch.object(asset_digital_human_services, "_poll_video", lambda _api_key, _video_id: video_payload),
                patch.object(asset_digital_human_services, "_download_to_path", fake_download),
            ):
                result = asset_digital_human_services.continue_video_agent_chat(SimpleNamespace(), Path(tmpdir), {"id": 5, "session_id": 6}, {
                    "provider_session_id": "session_123",
                    "prompt": "Approve",
                    "title": "测试",
                    "agent_confirm_generate": True,
                })

        self.assertEqual(result["provider_result"]["video_url"], video_payload["video_url"])
        self.assertEqual(result["provider_result"]["thumbnail_url"], video_payload["thumbnail_url"])
        self.assertEqual(result["provider_result"]["captioned_video_url"], video_payload["captioned_video_url"])
        self.assertEqual(result["provider_result"]["subtitle_url"], video_payload["subtitle_url"])
        approve_payload = next(call["payload"] for call in calls if call["path"] == "/v3/video-agents/session_123")
        self.assertIs(approve_payload["auto_proceed"], False)
        self.assertEqual(approve_payload["message"], "Approve")

    def test_video_agent_followup_retries_without_auto_proceed_when_provider_schema_rejects_it(self) -> None:
        from fastapi import HTTPException
        from opcrew_backend.koubo.koubo_storyboard import asset_digital_human_services

        calls: list[dict[str, object]] = []

        def fake_json_request(_api_key: str, method: str, path: str, payload: dict[str, object] | None = None, **_kwargs):
            body = dict(payload or {})
            calls.append({"method": method, "path": path, "payload": body})
            if body.get("auto_proceed") is False:
                raise HTTPException(status_code=502, detail='HeyGen POST /v3/video-agents/session_123 failed: HTTP 400: {"error":{"code":"invalid_parameter","message":"Extra inputs are not permitted","param":"auto_proceed"}}')
            return {"data": {"session_id": "session_123", "run_id": "run_123"}}

        with tempfile.TemporaryDirectory() as tmpdir:
            with (
                patch.object(asset_digital_human_services, "_heygen_key", lambda _ctx: "test-key"),
                patch.object(asset_digital_human_services, "_json_request", fake_json_request),
                patch.object(asset_digital_human_services, "_poll_video_agent_review", lambda _api_key, _session_id: {"session_id": "session_123", "status": "reviewing", "messages": []}),
            ):
                result = asset_digital_human_services.continue_video_agent_chat(SimpleNamespace(), Path(tmpdir), {"id": 5, "session_id": 6}, {
                    "provider_session_id": "session_123",
                    "prompt": "请增强表现力",
                    "agent_confirm_generate": False,
                })

        self.assertTrue(result["ok"])
        followup_payloads = [call["payload"] for call in calls if call["path"] == "/v3/video-agents/session_123"]
        self.assertEqual(followup_payloads[0]["auto_proceed"], False)
        self.assertEqual(followup_payloads[0]["message"], "不要生成视频，只修改计划\n请增强表现力")
        self.assertNotIn("auto_proceed", followup_payloads[1])
        self.assertEqual(followup_payloads[1]["message"], "不要生成视频，只修改计划\n请增强表现力")

    def test_avatar_delete_calls_provider_and_marks_local_records_deleted(self) -> None:
        from opcrew_backend.koubo.koubo_storyboard import asset_digital_human_services

        calls: list[dict[str, object]] = []

        def fake_json_request(_api_key: str, method: str, path: str, payload: dict[str, object] | None = None, **_kwargs):
            calls.append({"method": method, "path": path, "payload": dict(payload or {})})
            return {"data": {"deleted": True}}

        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            record_path = workspace / asset_digital_human_services.DIGITAL_HUMAN_AVATARS_REL / "avatar.json"
            record_path.parent.mkdir(parents=True, exist_ok=True)
            record_path.write_text(json.dumps({
                "source_path": "SessionOutput/storyboard/assets/images/avatar.png",
                "result": {
                    "data": {
                        "avatar_item": {
                            "id": "avatar_123",
                            "avatar_id": "avatar_123",
                            "name": "测试 Avatar",
                            "status": "completed",
                        },
                    },
                },
            }), encoding="utf-8")

            with (
                patch.object(asset_digital_human_services, "_heygen_key", lambda _ctx: "test-key"),
                patch.object(asset_digital_human_services, "_json_request", fake_json_request),
            ):
                result = asset_digital_human_services.delete_heygen_avatar_look(SimpleNamespace(), workspace, "avatar_123")

            self.assertTrue(result["ok"])
            self.assertEqual(result["deleted_local_records"], 1)
            self.assertEqual(calls[0]["method"], "DELETE")
            self.assertEqual(calls[0]["path"], "/v3/avatars/looks/avatar_123")
            updated = json.loads(record_path.read_text(encoding="utf-8"))
            self.assertIn("deleted_at", updated)
            self.assertEqual(updated["delete_result"], {"data": {"deleted": True}})
            self.assertEqual(asset_digital_human_services._local_avatar_items(workspace), [])

    def test_avatar_delete_route_and_frontend_are_wired(self) -> None:
        routes = ROUTES_PATH.read_text(encoding="utf-8")
        api = KOUBO_API_PATH.read_text(encoding="utf-8")
        modal = DIGITAL_HUMAN_AVATAR_MODAL_PATH.read_text(encoding="utf-8")
        css = DIGITAL_HUMAN_CSS_PATH.read_text(encoding="utf-8")

        for token in (
            "delete_heygen_avatar_look",
            "@router.delete(\"/api/koubo-storyboard/tasks/{task_id}/asset-library/digital-human/avatars/{avatar_id:path}\")",
            "koubo_storyboard.asset_library.digital_human.avatar.deleted",
        ):
            self.assertIn(token, routes)

        for token in (
            "deleteAssetLibraryDigitalHumanAvatar",
            "{ method: \"DELETE\" }",
        ):
            self.assertIn(token, api)

        for token in (
            "deleteAvatar",
            "删除 Avatar",
            "dh-card-actions",
            "FlowIcon name=\"delete\"",
            "props.onSelect?.(null)",
        ):
            self.assertIn(token, modal)

        for token in (
            ".dh-card-actions",
            ".dh-card-actions button.is-danger",
        ):
            self.assertIn(token, css)

    def test_photo_avatar_requested_as_avatar_v_falls_back_to_avatar_iv(self) -> None:
        from opcrew_backend.koubo.koubo_storyboard import asset_digital_human_services

        calls: list[dict[str, object]] = []

        def fake_json_request(_api_key: str, method: str, path: str, payload: dict[str, object] | None = None, **_kwargs):
            calls.append({"method": method, "path": path, "payload": dict(payload or {})})
            if method == "POST" and path == "/v3/videos":
                return {"data": {"video_id": "video_123"}}
            return {}

        def fake_download(_url: str, output_path: Path) -> None:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"video")

        avatar_item = {
            "id": "avatar_123",
            "avatar_type": "photo_avatar",
            "status": "completed",
            "supported_api_engines": ["avatar_iv"],
            "image_width": 1080,
            "image_height": 1920,
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            with (
                patch.object(asset_digital_human_services, "_heygen_key", lambda _ctx: "test-key"),
                patch.object(asset_digital_human_services, "_wait_for_avatar_ready", lambda *_args, **_kwargs: avatar_item),
                patch.object(asset_digital_human_services, "_json_request", fake_json_request),
                patch.object(asset_digital_human_services, "_poll_video", lambda _api_key, _video_id: {"status": "completed", "video_url": "https://files.heygen.ai/video/video_123.mp4", "duration": 6}),
                patch.object(asset_digital_human_services, "_download_to_path", fake_download),
            ):
                result = asset_digital_human_services.generate_digital_human_video(SimpleNamespace(), Path(tmpdir), {"id": 5, "session_id": 6}, {
                    "generation_model": "avatar_v",
                    "engine_type": "avatar_v",
                    "avatar_id": "avatar_123",
                    "avatar_type": "photo_avatar",
                    "supported_api_engines": ["avatar_iv"],
                    "voice_id": "voice_123",
                    "prompt": "测试口播",
                    "aspect": "9:16",
                })

            create_payload = next(call["payload"] for call in calls if call["path"] == "/v3/videos")
            self.assertNotIn("engine", create_payload)
            self.assertEqual(create_payload["expressiveness"], "low")
            self.assertEqual(result["requested_engine_type"], "avatar_v")
            self.assertEqual(result["engine_type"], "avatar_iv")
            self.assertEqual(result["model"], "Avatar IV")
            self.assertEqual(result["engine_fallback_reason"], "selected avatar does not support Avatar V")
            self.assertEqual(result["asset"]["source"], "digital_human")
            self.assertEqual(result["asset"]["label"], "Digital human video")
            public_asset_fields = {key: result["asset"][key] for key in ("id", "path", "label", "filename", "source")}
            self.assertNotIn("heygen", json.dumps(public_asset_fields, ensure_ascii=False).lower())
            from opcrew_backend.model_leakage_guard import sanitize_customer_payload
            from opcrew_backend.routes.auth import AUTH_ROLE_USER

            sanitized = sanitize_customer_payload(SimpleNamespace(), AUTH_ROLE_USER, result)
            sanitized_path = sanitized["asset"]["path"]
            self.assertEqual(sanitized_path, result["asset"]["path"])
            self.assertNotIn("[model]", sanitized_path)
            self.assertNotIn("heygen", sanitized_path.lower())
            self.assertTrue((Path(tmpdir) / sanitized_path).is_file())

    def test_avatar_iv_default_and_engine_compatibility_are_wired(self) -> None:
        services = (REPO_ROOT / "backend" / "opcrew_backend" / "koubo" / "koubo_storyboard" / "asset_digital_human_services.py").read_text(encoding="utf-8")
        routes = ROUTES_PATH.read_text(encoding="utf-8")
        model = (REPO_ROOT / "frontend" / "src" / "modules" / "koubo" / "UploadAssetLibrary" / "digitalHuman" / "digitalHumanModel.js").read_text(encoding="utf-8")
        panel = DIGITAL_HUMAN_PANEL_PATH.read_text(encoding="utf-8")
        settings_panel = (REPO_ROOT / "frontend" / "src" / "modules" / "koubo" / "UploadAssetLibrary" / "digitalHuman" / "DigitalHumanSettingsPanel.jsx").read_text(encoding="utf-8")

        for token in (
            'DEFAULT_AVATAR_ENGINE_TYPE = "avatar_iv"',
            "def _avatar_engine_type",
            "selected avatar does not support Avatar V",
        ):
            self.assertIn(token, services)

        for token in (
            '"model_name": _text(source.get("model_name"), "Avatar IV")',
            '"engine_type": _text(source.get("engine_type"), "avatar_iv")',
        ):
            self.assertIn(token, routes)

        for token in (
            'generation_model: "avatar_iv"',
            'model_name: "Avatar IV"',
            'engine_type: "avatar_iv"',
        ):
            self.assertIn(token, model)

        for token in (
            "function compatibleAvatarEngine",
            'hasPhotoAvatarInput ? "avatar_iv"',
            "supported_api_engines",
            "requested_engine_type",
        ):
            self.assertIn(token, panel)

        self.assertLess(settings_panel.find('id: "avatar_iv"'), settings_panel.find('id: "avatar_v"'))

    def test_frontend_video_agent_review_panel_and_revision_flow_are_wired(self) -> None:
        api = KOUBO_API_PATH.read_text(encoding="utf-8")
        overlay = DIGITAL_HUMAN_OVERLAY_PATH.read_text(encoding="utf-8")
        panel = DIGITAL_HUMAN_PANEL_PATH.read_text(encoding="utf-8")

        for token in (
            "assetLibraryDigitalHumanAgentSession",
            "?materialize=false",
            "stopAssetLibraryDigitalHumanAgentSession",
            "/asset-library/digital-human/agents/",
            "/stop",
        ):
            self.assertIn(token, api)

        for token in (
            "refreshAgentSession",
            "applyAgentResult",
            "dh-agent-plan",
            "dh-agent-timeline",
            "dh-agent-blueprint-card",
            "sessionTimeline",
            "blueprintTextFromSession",
            "currentAgentResources",
            "resource_ids",
            "visiblePlanText",
            "visibleMessages",
            "localAgentEvents",
            "sessionTimeline(currentAgentMessages(), localAgentEvents())",
            "agentEvent",
            "视频蓝图",
            "正在请求，会先返回可审阅的 Plan",
            "当前 Video Agent session 缺少 provider_session_id",
            "输入对当前 Video Agent Plan 的修改意见",
            "不要生成视频，只修改计划",
            "revisionOnlyPrompt",
            "dedupeContent",
            "content.slice(VIDEO_AGENT_REVISION_PREFIX.length)",
            "messageDedupeKey",
            "seenMessages = new Map()",
            "payload.generation_model === \"video_agent\" && payload.provider_session_id",
            "agentControlBusy",
            "canConfirmAgentGenerate",
            "const hasBlueprint = Boolean(currentBlueprintText())",
            "![\"generating\", \"completed\", \"failed\"].includes(status)",
            "notifyGeneratedAssets",
            "确认生成",
            "刷新中",
        ):
            self.assertIn(token, panel)

        for token in (
            ".dh-agent .dh-agent-plan-actions button",
            "color: #2563eb",
            "background: #fff",
        ):
            self.assertIn(token, DIGITAL_HUMAN_CSS_PATH.read_text(encoding="utf-8"))

        self.assertNotIn("disabled={!canConfirmAgent()}", panel)
        self.assertNotIn("完整 API 信息", panel)
        self.assertNotIn("currentAgentApiPayload", panel)
        self.assertNotIn("arrayItems(message?.resource_ids).join", panel)
        self.assertNotIn("HeyGen API 当前只返回 blueprint 索引", panel)
        self.assertNotIn("notifyGeneratedAssets(result);\n      setAutoGeneratePaused(false)", panel)
        self.assertNotIn("pauseVideoAgentSession", panel)
        self.assertNotIn("resumeVideoAgentSession", panel)
        self.assertNotIn("toggleAutoGeneratePaused", panel)
        self.assertNotIn("autoGeneratePaused", panel)
        self.assertNotIn("canControlAgentSession", panel)
        self.assertNotIn("已暂停 HeyGen Video Agent", panel)
        self.assertNotIn("已重新连接 HeyGen Video Agent", panel)
        self.assertNotIn("dh-agent-flow-meter", panel)
        self.assertNotIn("AUTO_GENERATE_SECONDS", panel)
        self.assertNotIn("formatCountdown", panel)
        self.assertNotIn("autoGenerateSeconds", panel)
        self.assertNotIn("setInterval", panel)
        self.assertNotIn("倒计时", panel)
        self.assertNotIn("下一步", panel)

        for token in (
            "isVideoAgentGenerationPayload",
            "removeDigitalHumanPendingVideo(payload.client_id)",
            "if (isVideoAgentGenerationPayload(payload)) return;",
            "if (isVideoAgentGenerationPayload(payload) && !completedAssets.length)",
            "source: \"digital-human-generating\"",
        ):
            self.assertIn(token, overlay)

        self.assertIn("props.onGenerationProgress?.({ ...requestPayload, ...event", panel)
        self.assertIn("props.onGenerationFailed?.({ ...requestPayload, ...event", panel)


if __name__ == "__main__":
    unittest.main()
