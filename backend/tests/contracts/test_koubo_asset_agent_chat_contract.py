from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

OPENCODE_ADAPTER_PATH = REPO_ROOT / "backend" / "opcrew_backend" / "adapters" / "opencode.py"
ASSET_ROUTES_PATH = REPO_ROOT / "backend" / "opcrew_backend" / "koubo" / "koubo_storyboard" / "asset_routes.py"
AGENT_COMMON_PATH = REPO_ROOT / "backend" / "opcrew_backend" / "koubo" / "koubo_storyboard" / "agent_chat_common.py"
AGENT_CHAT_ROUTES_PATH = REPO_ROOT / "backend" / "opcrew_backend" / "koubo" / "koubo_storyboard" / "agent_chat_routes.py"
PROVIDER_SERVICES_PATH = REPO_ROOT / "backend" / "opcrew_backend" / "koubo" / "koubo_storyboard" / "provider_services.py"
ASSET_VIDEO_GENERATION_SERVICES_PATH = REPO_ROOT / "backend" / "opcrew_backend" / "koubo" / "koubo_storyboard" / "asset_video_generation_services.py"
KOUBO_API_PATH = REPO_ROOT / "frontend" / "src" / "modules" / "koubo" / "KouboStoryBoard" / "kouboStoryboardApi.js"
OVERLAY_PATH = REPO_ROOT / "frontend" / "src" / "modules" / "koubo" / "UploadAssetLibrary" / "UploadAssetLibraryOverlay.jsx"
AGENT_PANEL_PATH = REPO_ROOT / "frontend" / "src" / "modules" / "koubo" / "UploadAssetLibrary" / "components" / "AgentPanel.jsx"
IMAGE_API_SETTINGS_PATH = REPO_ROOT / "frontend" / "src" / "modules" / "koubo" / "UploadAssetLibrary" / "components" / "ImageAPISettings.jsx"
IMAGES_AGENT_SETTINGS_PATH = REPO_ROOT / "frontend" / "src" / "modules" / "koubo" / "UploadAssetLibrary" / "components" / "ImagesAgentSettings.jsx"
VIDEO_AGENT_PANEL_PATH = REPO_ROOT / "frontend" / "src" / "modules" / "koubo" / "UploadAssetLibrary" / "components" / "VideoAgentPanel.jsx"
VIDEO_WORKSPACE_LIBRARY_PATH = REPO_ROOT / "frontend" / "src" / "modules" / "koubo" / "UploadAssetLibrary" / "components" / "VideoWorkspaceLibrary.jsx"
VIDEO_PICTURE_IN_PICTURE_PATH = REPO_ROOT / "frontend" / "src" / "modules" / "koubo" / "UploadAssetLibrary" / "videoPictureInPicture.js"
DIGITAL_HUMAN_WORKSPACE_PATH = REPO_ROOT / "frontend" / "src" / "modules" / "koubo" / "UploadAssetLibrary" / "digitalHuman" / "DigitalHumanWorkspace.jsx"
VIDEO_MODEL_CAPABILITIES_PATH = REPO_ROOT / "frontend" / "src" / "modules" / "koubo" / "UploadAssetLibrary" / "videoModelCapabilities.js"
AGENT_CHAT_CSS_PATH = REPO_ROOT / "frontend" / "src" / "modules" / "koubo" / "UploadAssetLibrary" / "styles" / "agent-chat.css"
MEDIA_GRID_CSS_PATH = REPO_ROOT / "frontend" / "src" / "modules" / "koubo" / "UploadAssetLibrary" / "styles" / "media-grid.css"
MEDIA_MODEL_CONFIG_PATH = REPO_ROOT / "ModelConfig" / "backend" / "opcrew_model_config" / "media_model_config.py"
VIDEO_AGENT_DESIGN_DOC_PATH = REPO_ROOT / "docs" / "koubo_upload_asset_video_agent_design.md"
IMAGE_TEMPLATE_DIR = REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "Reference" / "05_02"
ANALYSIS_VIDEO_GEMINI_PATH = REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "video_plan_executor_modules" / "video_gemini.py"
ANALYSIS_VIDEO_GROK_PATH = REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "video_plan_executor_modules" / "video_grok.py"


def load_opencode_adapter():
    spec = importlib.util.spec_from_file_location("opencode_adapter_contract", OPENCODE_ADAPTER_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class KouboAssetAgentChatContractTest(unittest.TestCase):
    def test_video_duration_custom_value_is_editable_and_confined_to_settings_panel(self) -> None:
        panel_source = VIDEO_AGENT_PANEL_PATH.read_text(encoding="utf-8")
        media_grid_css = MEDIA_GRID_CSS_PATH.read_text(encoding="utf-8")

        for token in (
            'updateSetting("duration", value)',
            "const commitDurationInput = (value) =>",
            "onBlur={(event) => commitDurationInput(event.currentTarget.value)}",
        ):
            self.assertIn(token, panel_source)

        for token in (
            "grid-template-columns: minmax(0, 1fr) 92px;",
            "grid-template-columns: minmax(0, 1fr) auto;",
            ".ual-setting-number input",
            "width: 100%;",
        ):
            self.assertIn(token, media_grid_css)

    def test_opencode_prompt_async_supports_per_prompt_tool_disable(self) -> None:
        source = OPENCODE_ADAPTER_PATH.read_text(encoding="utf-8")

        for token in (
            "tools: dict[str, bool] | None = None",
            "agent: str | None = None",
            "payload[\"tools\"] = tools",
            "payload[\"agent\"] = agent",
        ):
            self.assertIn(token, source)

    def test_opencode_event_filter_accepts_nested_message_session_id(self) -> None:
        module = load_opencode_adapter()

        self.assertEqual(module._opencode_event_session_id({
            "type": "message.updated",
            "properties": {
                "message": {
                    "info": {"sessionID": "ses_nested"},
                    "parts": [{"id": "part_1", "messageID": "msg_1", "sessionID": "ses_nested", "type": "text", "text": "done"}],
                },
            },
        }), "ses_nested")
        self.assertEqual(module._opencode_event_session_id({
            "type": "message.part.delta",
            "properties": {"messageID": "msg_1", "partID": "part_1", "sessionID": "ses_delta", "delta": "x"},
        }), "ses_delta")

    def test_backend_asset_agent_chat_routes_are_opencode_backed_and_tool_locked(self) -> None:
        source = ASSET_ROUTES_PATH.read_text(encoding="utf-8")
        common = AGENT_COMMON_PATH.read_text(encoding="utf-8")

        for token in (
            'IMAGE_API_SETTINGS_REL = "SessionContext/ImageAPISettings.json"',
            'IMAGES_AGENT_SETTINGS_REL = "SessionContext/ImagesAgentSettings.json"',
            'VIDEO_API_SETTINGS_REL = "SessionContext/VideoAPISettings.json"',
            'VIDEOS_AGENT_SETTINGS_REL = "SessionContext/VideosAgentSettings.json"',
            'IMAGE_API_WORKSPACE_HISTORY_REL = "SessionContext/ImageAPIWorkspaceHistory.json"',
            'VIDEO_API_WORKSPACE_HISTORY_REL = "SessionContext/VideoAPIWorkspaceHistory.json"',
            'LEGACY_AGENT_SETTINGS_REL = "SessionContext/AgentSettings.json"',
            'IMAGE_API_SETTINGS_SCHEMA = "upload_asset_library_image_api_settings_0.1"',
            'IMAGES_AGENT_SETTINGS_SCHEMA = "upload_asset_library_images_agent_settings_0.1"',
            'VIDEO_API_SETTINGS_SCHEMA = "upload_asset_library_video_api_settings_0.1"',
            'VIDEOS_AGENT_SETTINGS_SCHEMA = "upload_asset_library_videos_agent_settings_0.1"',
            'IMAGE_API_WORKSPACE_HISTORY_SCHEMA = "upload_asset_library_image_api_workspace_history_0.1"',
            'VIDEO_API_WORKSPACE_HISTORY_SCHEMA = "upload_asset_library_video_api_workspace_history_0.1"',
            "IMAGE_API_WORKSPACE_HISTORY_LIMIT = 500",
            "VIDEO_API_WORKSPACE_HISTORY_LIMIT = 500",
            "read_or_create_image_api_settings",
            "save_image_api_settings_payload",
            "read_or_create_image_api_workspace_history",
            "save_image_api_workspace_history_payload",
            "read_or_create_video_api_settings",
            "save_video_api_settings_payload",
            "read_or_create_video_api_workspace_history",
            "save_video_api_workspace_history_payload",
            "read_or_create_videos_agent_settings",
            "save_videos_agent_settings_payload",
            "/asset-library/image-api/settings",
            "/asset-library/image-api/history",
            "/asset-library/images-agent/settings",
            "/asset-library/video-api/settings",
            "/asset-library/video-api/history",
            "/asset-library/video-api/generate/events",
            "/asset-library/videos-agent/settings",
            "/asset-library/image-model-config",
            "/asset-library/video-model-config",
            "asset_library_default_image_config",
            "asset_library_image_model_config",
            "asset_library_video_model_config",
            'load_config(deps.ctx, "video")',
            "load_agent_model_aliases(deps.ctx)",
            "customer_media_public_config",
            '"agentImageAlias"',
            '"agentVideoAlias"',
            "VIDEO_SETTING_ASPECTS",
            "SessionContext/Variables.json",
            "default_image_config",
            "Image_GPT.md",
            "Image_Gemini.md",
            "Image_Grok.md",
            "IMAGE_GPT",
            "IMAGE_GEMINI",
            "GROK",
            "import ast",
            "chat_opencode_session_id",
            "chat_last_model_image_generation",
            "chat_agent_image_generation_keys",
            "ASSET_AGENT_REFERENCE_ROLES",
            "ASSET_AGENT_CHAT_DISABLED_TOOLS",
            "ASSET_AGENT_CHAT_DISABLED_TOOLS = KOUBO_AGENT_CHAT_DISABLED_TOOLS",
            "ASSET_AGENT_CHAT_MODEL_ROLE = AUTH_ROLE_USER",
            "SURFACE_KOUBO_ASSET_AGENT_CHAT",
            "resolve_model(session_row, provider, model_id, ASSET_AGENT_CHAT_MODEL_ROLE, SURFACE_KOUBO_ASSET_AGENT_CHAT, sc=deps)",
            "mask_model_fields_for_role(deps.ctx, ASSET_AGENT_CHAT_MODEL_ROLE, SURFACE_KOUBO_ASSET_AGENT_CHAT, model)",
            "tools=ASSET_AGENT_CHAT_DISABLED_TOOLS",
            "opencode_event_has_tool_use",
            "asset_agent.chat.tool_blocked",
            "/asset-library-agent/chat/ensure-session",
            "/asset-library-agent/chat/message",
            "/asset-library-agent/chat/events",
            "/asset-library-agent/chat/abort",
            "/asset-library-agent/chat/messages",
            "<PROMPT_CANDIDATE>",
            "IMAGE_GENERATION_REQUEST",
            "asset_agent_model_supports_image_generation",
            "asset_agent.image_generation.completed",
            "claim_agent_image_generation_key",
            "enqueue_existing_agent_image_generations",
            "koubo_storyboard.asset_library_agent.image.catchup_failed",
            "generate_asset_library_image(task_id",
            "Select an Agent image model before generating.",
            "asset_agent_effective_image_prompt",
            "image_aspect_for_request",
            "requested_aspect",
            "Output aspect is 9:16 portrait",
            "do not stretch the reference canvas into the output",
            '"aspect": request_payload["aspect"]',
            "reference_image_roles",
            "ast.literal_eval(raw_item)",
            "User/spoken words are semantic guidance only",
            "safe_opencode_message",
            "sanitize_opencode_event",
        ):
            self.assertIn(token, source)

        for token in (
            "KOUBO_AGENT_CHAT_DISABLED_TOOLS",
            '"bash": False',
            '"read": False',
            '"write": False',
            '"websearch": False',
            "def opencode_event_has_tool_use",
            "def sanitize_opencode_event",
        ):
            self.assertIn(token, common)

    def test_asset_and_prompt_agent_chat_autoheal_stale_opencode_sessions(self) -> None:
        asset_source = ASSET_ROUTES_PATH.read_text(encoding="utf-8")
        agent_source = AGENT_CHAT_ROUTES_PATH.read_text(encoding="utf-8")

        for source in (asset_source, agent_source):
            self.assertIn("from urllib.error import HTTPError", source)
            self.assertIn("def opencode_session_not_found", source)
            self.assertIn("return isinstance(exc, HTTPError) and exc.code == 404", source)
            self.assertIn('"recovered": True', source)

        for token in (
            "def clear_stale_asset_agent_chat_session",
            '"koubo_storyboard.asset_library_agent.chat.session.stale_cleared"',
            "clear_stale_asset_agent_chat_session(task, chat_session_id, \"ensure_session\")",
            "clear_stale_asset_agent_chat_session(task, chat_session_id, \"messages\")",
            "opencode_client_for(asset_agent_session_row(task), sc=deps).messages(chat_session_id, limit=1)",
        ):
            self.assertIn(token, asset_source)

        for token in (
            "def clear_stale_agent_chat_session",
            '"koubo_storyboard.agent_chat.session.stale_cleared"',
            "clear_stale_agent_chat_session(task, agent_key, chat_session_id, \"ensure_session\")",
            "clear_stale_agent_chat_session(task, key, chat_session_id, \"messages\")",
            "opencode_client_for(agent_chat_session_row(task), sc=deps).messages(chat_session_id, limit=1, timeout=3)",
            '"koubo_storyboard.agent_chat.session_probe_skipped"',
            'if "opencode_session_id" in state or "chat_opencode_session_id" in state:',
        ):
            self.assertIn(token, agent_source)

    def test_provider_resolver_accepts_explicit_surface(self) -> None:
        source = PROVIDER_SERVICES_PATH.read_text(encoding="utf-8")

        for token in (
            "surface: str = SURFACE_KOUBO_HOST_PRODUCT_PROMPT",
            "resolve_prompt_model_for_role(sc.ctx, role, surface",
        ):
            self.assertIn(token, source)

    def test_video_api_uses_analysis_v1_style_environment_urlopen(self) -> None:
        source = ASSET_VIDEO_GENERATION_SERVICES_PATH.read_text(encoding="utf-8")

        for token in (
            "Match Analysis_V1 video tools",
            "leaving TUN/system proxy",
            "return urllib.request.urlopen(req, timeout=timeout)",
            '"network_mode": "environment_urlopen"',
            "return 4 if requested <= 4 else 8",
            ":predictLongRunning?key=",
            "normalized_first = normalized_image_reference(first_image, output_path, aspect)",
            'instance["image"] = {"mimeType": inline["mimeType"], "bytesBase64Encoded": inline["bytesBase64Encoded"]}',
            '"aspectRatio": aspect',
            "provider=provider",
        ):
            self.assertIn(token, source)
        self.assertNotIn("OPENCREW_MIHOMO_PROXY_URL", source)
        self.assertNotIn("resolved_provider_urlopen", source)
        gemini_section = source.split('if provider == "gemini":', 1)[1].split('elif provider == "wan":', 1)[0]
        self.assertNotIn("ProxyHandler({})", gemini_section)
        self.assertNotIn("lastFrame", gemini_section)

    def test_video_api_uses_analysis_v1_video_grok_xai_call_shape(self) -> None:
        source = ASSET_VIDEO_GENERATION_SERVICES_PATH.read_text(encoding="utf-8")
        xai_section = source.split('elif provider == "xai":', 1)[1].split("else:", 1)[0]

        for token in (
            "Match Video_Grok.py",
            "video_proxy_tunnel_error",
            "urllib.request.build_opener(urllib.request.ProxyHandler({}))",
            '"https://api.x.ai/v1/videos/generations"',
            '"duration": seconds',
            '"aspect_ratio": aspect',
            "requested_resolution = xai_video_resolution(config, model)",
            '"resolution": requested_resolution',
            'provider_meta["resolution"] = requested_resolution',
            'reference_count = int(first_image is not None) if provider == "xai"',
            'payload["image"] = {"url": f"data:{inline[\'mimeType\']};base64,{inline[\'bytesBase64Encoded\']}"}',
            'started.get("request_id") or started.get("id")',
            'f"https://api.x.ai/v1/videos/{urllib.parse.quote(video_id, safe=\'\')}"',
            "operation_done(polled)",
            "download_video_binary(video_url, output_path, {\"Authorization\": f\"Bearer {api_key}\"}, provider=provider)",
        ):
            self.assertIn(token, source)
        self.assertIn("video_id and time.time() < deadline", xai_section)
        self.assertNotIn("reference_images", xai_section)

    def test_video_workspace_library_download_menu_is_wired(self) -> None:
        source = VIDEO_WORKSPACE_LIBRARY_PATH.read_text(encoding="utf-8")

        for token in (
            "function assetDownloadName",
            "function downloadUrl",
            "href={downloadUrl(src())}",
            "download={assetDownloadName(asset())}",
            "<FlowIcon name=\"download\" />Download",
        ):
            self.assertIn(token, source)

    def test_video_workspace_picture_in_picture_bootstraps_lazy_preview(self) -> None:
        helper_source = VIDEO_PICTURE_IN_PICTURE_PATH.read_text(encoding="utf-8")
        for path in (VIDEO_WORKSPACE_LIBRARY_PATH, DIGITAL_HUMAN_WORKSPACE_PATH):
            source = path.read_text(encoding="utf-8")
            for token in (
                "browserSupportsVideoPictureInPicture",
                "toggleVideoPictureInPicture",
                "const canPictureInPicture = () => Boolean(src() && browserSupportsVideoPictureInPicture())",
                "if (!menuOpen()) activatePreview()",
                "activatePreview(() =>",
                "toggleVideoPictureInPicture(videoEl).catch",
            ):
                self.assertIn(token, source)
            self.assertNotIn("&& videoEl\n    && !videoEl.disablePictureInPicture", source)
        for token in (
            "document.pictureInPictureEnabled",
            "prototype?.requestPictureInPicture",
            "prototype?.webkitSetPresentationMode",
            'videoEl.addEventListener("loadedmetadata"',
            "await waitForVideoMetadata(videoEl)",
            'videoEl.webkitSetPresentationMode(mode)',
            "await videoEl.requestPictureInPicture()",
        ):
            self.assertIn(token, helper_source)

    def test_analysis_v1_video_modules_forward_requested_aspect(self) -> None:
        gemini_source = ANALYSIS_VIDEO_GEMINI_PATH.read_text(encoding="utf-8")
        grok_source = ANALYSIS_VIDEO_GROK_PATH.read_text(encoding="utf-8")

        for source in (gemini_source, grok_source):
            self.assertIn("def normalize_video_aspect", source)
            self.assertIn('context.get("aspect") or context.get("aspect_ratio") or context.get("requested_aspect")', source)
            self.assertIn('"aspect": aspect', source)
        self.assertIn('"aspectRatio": aspect', gemini_source)
        self.assertIn('"aspect_ratio": aspect', grok_source)
        self.assertNotIn('"aspectRatio": "9:16"', gemini_source)
        self.assertNotIn('"aspect_ratio": "9:16"', grok_source)

    def test_frontend_agent_chat_api_overlay_and_panel_are_wired(self) -> None:
        api_source = KOUBO_API_PATH.read_text(encoding="utf-8")
        overlay_source = OVERLAY_PATH.read_text(encoding="utf-8")
        panel_source = AGENT_PANEL_PATH.read_text(encoding="utf-8")
        image_api_settings_source = IMAGE_API_SETTINGS_PATH.read_text(encoding="utf-8")
        images_agent_settings_source = IMAGES_AGENT_SETTINGS_PATH.read_text(encoding="utf-8")
        video_agent_panel_source = VIDEO_AGENT_PANEL_PATH.read_text(encoding="utf-8")
        agent_chat_css_source = AGENT_CHAT_CSS_PATH.read_text(encoding="utf-8")
        asset_routes_source = ASSET_ROUTES_PATH.read_text(encoding="utf-8")
        agent_chat_routes_source = AGENT_CHAT_ROUTES_PATH.read_text(encoding="utf-8")

        for token in (
            "assetLibraryImageModelConfig",
            "assetLibraryVideoModelConfig",
            "assetLibraryImageAPISettings",
            "saveAssetLibraryImageAPISettings",
            "assetLibraryImageAPIHistory",
            "saveAssetLibraryImageAPIHistory",
            "assetLibraryImagesAgentSettings",
            "saveAssetLibraryImagesAgentSettings",
            "assetLibraryVideoAPISettings",
            "saveAssetLibraryVideoAPISettings",
            "assetLibraryVideoAPIHistory",
            "saveAssetLibraryVideoAPIHistory",
            "assetLibraryVideosAgentSettings",
            "saveAssetLibraryVideosAgentSettings",
            "streamAssetLibraryVideoGenerate",
            "assetLibraryAgentChatEnsureSession",
            "assetLibraryAgentChatMessages",
            "assetLibraryAgentChatSendMessage",
            "assetLibraryAgentChatAbort",
            "assetLibraryAgentChatEventsUrl",
        ):
            self.assertIn(token, api_source)

        for token in (
            "imageModelConfig={loadAssetLibraryImageModelConfig}",
            "loadImageAPISettings={loadImageAPISettings}",
            "saveImageAPISettings={saveImageAPISettings}",
            "loadImageAPIHistory={loadImageAPIHistory}",
            "saveImageAPIHistory={saveImageAPIHistory}",
            "loadImagesAgentSettings={loadImagesAgentSettings}",
            "saveImagesAgentSettings={saveImagesAgentSettings}",
            "videoModelConfig={loadAssetLibraryVideoModelConfig}",
            "loadVideoAPISettings={loadVideoAPISettings}",
            "saveVideoAPISettings={saveVideoAPISettings}",
            "loadVideoAPIHistory={loadVideoAPIHistory}",
            "saveVideoAPIHistory={saveVideoAPIHistory}",
            "loadVideosAgentSettings={loadVideosAgentSettings}",
            "saveVideosAgentSettings={saveVideosAgentSettings}",
            "ensureAgentChatSession={ensureAgentChatSession}",
            "loadAgentChatMessages={loadAgentChatMessages}",
            "sendAgentChatMessage={sendAgentChatMessage}",
            "abortAgentChat={abortAgentChat}",
            "agentChatEventsUrl={agentChatEventsUrl}",
            "onAgentImageGenerationEvent={handleAgentImageGenerationEvent}",
            "generateVideo={generateVideo}",
            "streamAssetLibraryVideoGenerate",
            "chat_opencode_session_id: generationOptions.chatOpenCodeSessionId",
            "prompt_candidate_id: generationOptions.promptCandidateId",
            "aspect,",
        ):
            self.assertIn(token, overlay_source)

        for token in (
            "ImageAPISettings",
            "ImagesAgentSettings",
            "props.loadImageAPISettings",
            "props.loadImagesAgentSettings",
            "props.saveImageAPISettings",
            "props.saveImagesAgentSettings",
            "props.loadImageAPIHistory",
            "props.saveImageAPIHistory",
            "directHistorySaveSnapshot",
            "directHistoryReady",
            "initializeDirectImageHistory",
            "queueDirectImageHistorySave",
            "directImageHistoryMessagesForSave",
            "hydrateDirectImageHistoryMessage",
            "imageAPISettingsPayload",
            "imagesAgentSettingsPayload",
            "openSettings(\"image-api\")",
            "openSettings(\"images-agent\")",
            "partsByMessageId",
            "message.part.delta",
            "EventSource",
            "selectedChatModelKey",
            "chatModelItems",
            "selectedChatModel()",
            "chatProvider",
            "chatModel",
            "chatModelItems={chatModelItems}",
            "agentImageModels",
            "requireAgentImageModel",
            "agentImageAlias",
            "<PROMPT_CANDIDATE>",
            "IMAGE_GENERATION_REQUEST",
            "asset_agent.image_generation.completed",
            "extractPromptCandidates",
            "confirmBeforeGenerate",
            "sendAgentChatMessage",
            "abortAgentChat",
            "promptCandidateId",
            "referencePayloadItems()",
            "reference_role",
            "TARGET_FRAME",
        ):
            self.assertIn(token, panel_source)
        self.assertNotIn("Generate image from Images-Agent prompt", panel_source)

        for token in (
            "props.loadVideoAPISettings",
            "props.saveVideoAPISettings",
            "props.loadVideoAPIHistory",
            "props.saveVideoAPIHistory",
            "initializeDirectHistory",
            "queueDirectHistorySave",
            "directHistorySaveSnapshot = messages",
            "if (!directHistoryReady()) return",
            "directHistoryMessagesForSave",
            "VIDEO_WORKSPACE_HISTORY_LIMIT = 500",
            "slice(-VIDEO_WORKSPACE_HISTORY_LIMIT)",
            "hydrateDirectHistoryMessage",
            "props.loadVideosAgentSettings",
            "props.saveVideosAgentSettings",
            "props.videoModelConfig",
            "video_generation_settings",
            "agentVideoAlias",
            "videoAgentModels",
            "agent_model_aliases",
            "视频模型",
            "请先配置视频模型",
            "props.generateVideo",
            "每次确认",
            "不确认",
            '["9:16", "16:9"]',
            "[4, 8, 15]",
            "videoCapability().params.count.enabled",
            "videoCapability().params.count.values",
            "referenceMode",
            "confirmBeforeGenerate",
            "duration",
            "ual-composer-box",
            'mode: "video"',
            "openPromptBuilder",
            "智能体模型",
            "selectedSettingsModelKey",
            "selectAgentModel",
            "parseVideoAgentDisplay",
            "replayAssetVideoGenerationEvents",
            "renderVideoMessage",
            "renderThinking",
            "renderDebugDetails",
            "renderResultCard",
            "renderReferenceStrip",
            "ual-user-bubble",
            "ual-assistant-bubble",
            "ual-message-thinking",
            "ual-message-debug",
            "ual-result-card is-video",
            "VIDEO_GENERATION_REQUEST",
            "formatDebugText",
        ):
            self.assertIn(token, video_agent_panel_source)
        for token in (
            "asset_video_request_context",
            "asset_video_generation_events",
            "video_generation_message_from_event",
            "re.IGNORECASE",
            "record_agent_video_generation_event",
        ):
            self.assertIn(token, agent_chat_routes_source)
        self.assertIn(".ual-result-card.is-video", agent_chat_css_source)
        self.assertNotIn("visibleMessageText(message", video_agent_panel_source)
        for token in ("hasSavedConfig", "active_video_config", "No Video Config model configured"):
            self.assertNotIn(token, video_agent_panel_source)
        for token in ('"4:3"', '"1:1"', '"3:4"', "[1, 2, 3, 4]", 'type="checkbox"'):
            self.assertNotIn(token, video_agent_panel_source)

        self.assertIn("API Settings", image_api_settings_source)
        self.assertNotIn("Run model", image_api_settings_source)
        self.assertIn("Image Models", image_api_settings_source)

        for token in (
            "prompt_builder_model_looks_video",
            "Video_Gemini.md",
            "Ref_05_02_Video_Gemini.md",
            "build_video_prompt_builder_package",
            "copy_video_prompt_template_snapshot_for_settings",
            "video_prompt_builder.template_snapshotted",
            "prompt_template_snapshot",
            "VIDEO_PROMPT_BUILDER_POSITIVE_OVERRIDE",
            "VIDEO_PROMPT_BUILDER_NEGATIVE_OVERRIDE",
            "VIDEO_PROMPT_BUILDER_PROMPT_OVERRIDE",
            "template_path",
            "Ref_05_02_Video_",
            'payload, "mode": "video"',
        ):
            self.assertIn(token, asset_routes_source)
        self.assertIn("template_path: promptBuilderPayload().template_path", video_agent_panel_source)
        self.assertNotIn("VIDEO_PROMPT_BUILDER_JSON", asset_routes_source)
        self.assertNotIn("VideoPrompt.json", asset_routes_source)
        self.assertNotIn("Draft_{request_id}_VideoPrompt.json", asset_routes_source)
        self.assertNotIn("Applied_{request_id}_VideoPrompt.json", asset_routes_source)
        self.assertNotIn("chatModelItems", image_api_settings_source)
        self.assertIn("agent_model_aliases", image_api_settings_source)
        self.assertIn("agentImageAlias", image_api_settings_source)
        self.assertIn("Configure Agent image models first", image_api_settings_source)
        self.assertNotIn("No active image provider", image_api_settings_source)
        self.assertIn("Agent settings", images_agent_settings_source)
        self.assertNotIn("Run model", images_agent_settings_source)
        self.assertIn("Image Models", images_agent_settings_source)
        self.assertIn("Agent Models", images_agent_settings_source)
        self.assertIn("chatModelItems", images_agent_settings_source)
        self.assertIn("agent_model_aliases", images_agent_settings_source)
        self.assertIn("agentImageAlias", images_agent_settings_source)
        self.assertIn("Configure Agent image models first", images_agent_settings_source)
        self.assertNotIn("No active image provider", images_agent_settings_source)

    def test_asset_agent_image_generation_uses_alias_when_model_fields_are_masked(self) -> None:
        asset_routes_source = ASSET_ROUTES_PATH.read_text(encoding="utf-8")
        panel_source = AGENT_PANEL_PATH.read_text(encoding="utf-8")
        overlay_source = OVERLAY_PATH.read_text(encoding="utf-8")

        for token in (
            "def resolve_agent_image_model_payload",
            "agent_image_alias_from_payload",
            'payload.get("agentImageAlias")',
            "load_agent_model_aliases(deps.ctx)",
            "Select a valid Agent image model before generating.",
            "requested_provider, requested_model = resolve_agent_image_model_payload(payload)",
            '"agentImageAlias": agent_settings.get("agentImageAlias")',
        ):
            self.assertIn(token, asset_routes_source)
        for token in (
            "provider: alias ? \"\" : text(model.provider)",
            "model: alias ? \"\" : text(model.model)",
            "agentImageAlias: imageModel.alias",
            "provider: imageModel.provider",
            "model: imageModel.model",
        ):
            self.assertIn(token, panel_source)
        self.assertIn('agentImageAlias: generationOptions.agentImageAlias || ""', overlay_source)

    def test_asset_video_agent_uses_alias_when_model_fields_are_masked(self) -> None:
        asset_routes_source = ASSET_ROUTES_PATH.read_text(encoding="utf-8")
        services_source = ASSET_VIDEO_GENERATION_SERVICES_PATH.read_text(encoding="utf-8")
        video_panel_source = VIDEO_AGENT_PANEL_PATH.read_text(encoding="utf-8")
        capability_source = VIDEO_MODEL_CAPABILITIES_PATH.read_text(encoding="utf-8")
        agent_panel_source = AGENT_PANEL_PATH.read_text(encoding="utf-8")
        overlay_source = OVERLAY_PATH.read_text(encoding="utf-8")

        for token in (
            "def resolve_agent_video_model_payload",
            "agent_video_alias_from_payload",
            "def video_api_generation_source",
            "settings_paths = [VIDEO_API_SETTINGS_REL, VIDEOS_AGENT_SETTINGS_REL]",
            "settings_paths = [VIDEOS_AGENT_SETTINGS_REL, VIDEO_API_SETTINGS_REL]",
            "scope = deps.text(source.get(\"settingsScope\") or source.get(\"settings_scope\") or source.get(\"video_settings_scope\"))",
            'load_agent_model_aliases(deps.ctx, "video")',
            "return resolve_agent_video_model_payload(payload)",
            "generation_source = video_api_generation_source(task, source)",
            "public_agent_video_alias = agent_video_alias_from_payload(generation_source)",
            "if public_agent_video_alias:",
            "resolve_agent_video_model_payload(generation_source)",
            "resolve_agent_video_model_payload(settings, strict_alias=False)",
            'public_provider = "" if public_agent_video_alias else deps.text(source.get("provider"))',
            'public_model = "" if public_agent_video_alias else deps.text(source.get("model"))',
            '"agentVideoAlias": public_agent_video_alias',
            '"provider": public_provider',
            '"model": public_model',
            "customer_media_public_config",
            'customer_media_public_config(load_config(deps.ctx, "video"), "video")',
        ):
            self.assertIn(token, asset_routes_source)
        generate_section = asset_routes_source.split("async def asset_library_video_api_generate_events", 1)[1].split("deps.add_event", 1)[0]
        self.assertNotIn("Video API generation requires a selected Video Config model", generate_section)
        self.assertNotIn("if not requested_provider or not requested_model", generate_section)
        self.assertNotIn('"provider": requested_provider', generate_section)
        self.assertNotIn('"model": requested_model', generate_section)
        for token in (
            "def resolve_agent_video_alias",
            'load_agent_model_aliases(sc.ctx, "video")',
            "request_alias = agent_video_alias_from_payload(payload)",
            "alias_provider, alias_model = resolve_agent_video_alias(settings_alias, strict=bool(request_alias), sc=sc)",
        ):
            self.assertIn(token, services_source)
        for token in (
            "function videoModelSelectionPayload",
            "provider: alias ? \"\" : text(item.provider)",
            "model: alias ? \"\" : text(item.model)",
            "const agentVideoAlias = promptBuilderPayload().agentVideoAlias || settings().agentVideoAlias || \"\"",
            "provider: agentVideoAlias ? \"\" : (promptBuilderPayload().provider || settings().provider || \"\")",
            "model: agentVideoAlias ? \"\" : (promptBuilderPayload().model || settings().model || \"\")",
            "provider: sourceSettings.agentVideoAlias ? \"\" : (request.provider || sourceSettings.provider || \"\")",
            "settingsScope: \"videos_agent\"",
            "settingsScope: isAgent() ? \"videos_agent\" : \"video_api\"",
            "title={model.alias || \"Video model\"}",
            "agentVideoAlias,",
            "if (provider && model)",
            "videoAgentModelSupportsText(config, item)",
        ):
            self.assertIn(token, video_panel_source)
        self.assertNotIn("provider: selected.provider || \"\"", video_panel_source)
        self.assertNotIn("model: selected.model || \"\"", video_panel_source)
        self.assertNotIn("provider: item.provider || \"\"", video_panel_source)
        self.assertNotIn("model: item.model || \"\"", video_panel_source)
        for token in (
            "function aliasCapability",
            "if (!model) return null;",
            "export function videoAgentModelSupportsText",
            "if (provider && model)",
        ):
            self.assertIn(token, capability_source)
        for token in (
            "const isGrokImageModel",
            'aliasKey === "qualityx"',
            "agentImageModels(config).find((item) => isGrokImageModel(item))",
        ):
            self.assertIn(token, agent_panel_source)
        self.assertIn('agentVideoAlias: generationOptions.agentVideoAlias || ""', overlay_source)
        self.assertIn('settingsScope: generationOptions.settingsScope || ""', overlay_source)
        for token in (
            "Gemini video generation failed",
            "Wan video generation failed",
            "Seedance video generation failed",
            "OpenAI video generation failed",
            "xAI video generation failed",
            "OpenRouter video generation failed",
            "Wan R2V video generation failed",
            "Chanjing HappyHorse video generation failed",
        ):
            self.assertNotIn(token, services_source)

    def test_asset_library_image_video_model_config_returns_public_aliases_only(self) -> None:
        import asyncio
        import json
        from types import SimpleNamespace
        from unittest.mock import patch

        from fastapi import APIRouter, FastAPI
        from opcrew_backend.koubo.koubo_storyboard import asset_routes

        raw_image_config = {
            "kind": "image",
            "active_provider": "gemini",
            "providers": [
                {
                    "provider": "gemini",
                    "provider_label": "Gemini",
                    "label": "Gemini",
                    "model": "gpt-image-2",
                    "has_api_key": True,
                    "api_key_ref": "image_gemini_key",
                    "base_url": "https://generativelanguage.googleapis.com",
                    "models": [
                        {
                            "model": "grok-imagine-image-quality",
                            "label": "gpt-image-2",
                            "description": "Gemini image model via OpenRouter",
                        }
                    ],
                }
            ],
            "agent_model_aliases": [
                {"alias": "Image Alias 01", "provider": "gemini", "model": "grok-imagine-image-quality"}
            ],
        }
        raw_video_config = {
            "kind": "video",
            "active_provider": "openrouter",
            "providers": [
                {
                    "provider": "openrouter",
                    "provider_label": "Kling",
                    "label": "Kling",
                    "model": "kling2.5",
                    "has_api_key": True,
                    "api_key_ref": "video_kling_key",
                    "base_url": "https://openrouter.ai/api/v1",
                    "models": [
                        {
                            "model": "grok-imagine-video-1.5-preview",
                            "label": "grok-imagine-video-1.5-preview",
                            "description": "Veo models via Gemini API",
                            "price_summary": "Volcano Ark Seedance 2.0",
                            "input_modes": ["text", "image"],
                            "duration": {"values": [4, 8], "label": "Kling duration"},
                            "reference_images": {"min": 0, "max": 2, "provider_label": "Gemini"},
                        }
                    ],
                }
            ],
            "agent_model_aliases": [
                {"alias": "Video Alias 01", "provider": "openrouter", "model": "grok-imagine-video-1.5-preview"}
            ],
        }

        def fake_load_config(_ctx: object, kind: str) -> dict[str, object]:
            return raw_video_config if kind == "video" else raw_image_config

        async def asgi_get_json(app: FastAPI, path: str) -> tuple[int, dict[str, object]]:
            response_status = 0
            response_body = bytearray()
            sent_request = False

            async def receive() -> dict[str, object]:
                nonlocal sent_request
                if not sent_request:
                    sent_request = True
                    return {"type": "http.request", "body": b"", "more_body": False}
                await asyncio.sleep(0)
                return {"type": "http.disconnect"}

            async def send(message: dict[str, object]) -> None:
                nonlocal response_status
                if message["type"] == "http.response.start":
                    response_status = int(message["status"])
                elif message["type"] == "http.response.body":
                    response_body.extend(message.get("body", b""))

            await app(
                {
                    "type": "http",
                    "asgi": {"version": "3.0"},
                    "http_version": "1.1",
                    "method": "GET",
                    "scheme": "http",
                    "path": path,
                    "raw_path": path.encode("ascii"),
                    "query_string": b"",
                    "headers": [],
                    "client": ("testclient", 50000),
                    "server": ("testserver", 80),
                },
                receive,
                send,
            )
            return response_status, json.loads(response_body.decode("utf-8"))

        app = FastAPI()
        router = APIRouter()
        deps = SimpleNamespace(ctx=SimpleNamespace(), task_or_404=lambda task_id: {"id": task_id, "session_id": 77})
        with patch.object(asset_routes, "load_config", fake_load_config):
            asset_routes.register_asset_routes(router, deps)
            app.include_router(router)
            image_status, image_body = asyncio.run(asgi_get_json(app, "/api/koubo-storyboard/tasks/42/asset-library/image-model-config"))
            video_status, video_body = asyncio.run(asgi_get_json(app, "/api/koubo-storyboard/tasks/42/asset-library/video-model-config"))

        self.assertEqual(image_status, 200)
        self.assertEqual(video_status, 200)
        self.assertEqual(image_body["providers"], [])
        self.assertEqual(video_body["providers"], [])
        self.assertEqual(image_body["agent_model_aliases"][0]["agentImageAlias"], "Image Alias 01")
        self.assertEqual(video_body["agent_model_aliases"][0]["agentVideoAlias"], "Video Alias 01")
        self.assertEqual(video_body["agent_model_aliases"][0]["capability"]["input_modes"], ["text", "image"])
        self.assertEqual(video_body["agent_model_aliases"][0]["capability"]["duration"], {"values": [4, 8]})
        for body in (image_body, video_body):
            serialized = json.dumps(body, ensure_ascii=False).lower()
            for token in (
                "grok-imagine",
                "gpt-image",
                "kling",
                "kling2.5",
                "veo",
                "gemini",
                "seedance",
                "volcano",
                "openrouter",
                "api_key_ref",
                "base_url",
                "video_kling_key",
                "image_gemini_key",
            ):
                self.assertNotIn(token, serialized)

    def test_public_video_model_config_builds_resolvable_neutral_aliases_without_saved_aliases(self) -> None:
        import json

        from opcrew_backend.routes.media_model_config import customer_media_public_alias_target, customer_media_public_config

        raw_video_config = {
            "kind": "video",
            "active_provider": "openrouter",
            "providers": [
                {
                    "provider": "openrouter",
                    "provider_label": "Kling",
                    "label": "Kling",
                    "model": "bytedance/seedance-2.0",
                    "enabled": True,
                    "has_api_key": True,
                    "api_key_ref": "video_openrouter_key",
                    "base_url": "https://openrouter.ai/api/v1",
                    "models": [
                        {
                            "model": "bytedance/seedance-2.0",
                            "label": "Volcano Ark Seedance 2.0",
                            "description": "Veo models via Gemini API",
                            "input_modes": ["text", "image"],
                        }
                    ],
                }
            ],
            "agent_model_aliases": [],
        }

        public_config = customer_media_public_config(raw_video_config, "video")
        aliases = public_config["agent_model_aliases"]

        self.assertEqual(aliases[0]["alias"], "视频模型 01")
        self.assertEqual(aliases[0]["label"], "视频模型 01")
        self.assertEqual(aliases[0]["agentVideoAlias"], "视频模型 01")
        self.assertEqual(
            customer_media_public_alias_target(raw_video_config, "video", "视频模型 01"),
            ("openrouter", "bytedance/seedance-2.0"),
        )
        serialized = json.dumps(public_config, ensure_ascii=False).lower()
        for token in ("openrouter", "seedance", "kling", "volcano", "veo", "gemini", "api_key_ref", "base_url"):
            self.assertNotIn(token, serialized)

    def test_video_api_generate_events_accepts_alias_only_without_sse_model_leak(self) -> None:
        import asyncio
        import json
        import tempfile
        from types import SimpleNamespace
        from unittest.mock import patch

        from fastapi import APIRouter, FastAPI
        from opcrew_backend.koubo.koubo_storyboard import asset_routes

        events: list[dict[str, object]] = []
        generated_payloads: list[dict[str, object]] = []

        def text(value: object, fallback: str = "") -> str:
            raw = str(value or "").strip()
            return raw or fallback

        def add_event(session_id: int, kind: str, payload: dict[str, object]) -> None:
            events.append({"session_id": session_id, "kind": kind, "payload": dict(payload)})

        def generate_asset_library_video(_task_id: int, payload: dict[str, object], **_kwargs) -> dict[str, object]:
            generated_payloads.append(dict(payload))
            return {
                "asset": {
                    "id": "SessionOutput/storyboard/assets/videos/out.mp4",
                    "path": "SessionOutput/storyboard/assets/videos/out.mp4",
                    "source": "video_api",
                },
                "output": "SessionOutput/storyboard/assets/videos/out.mp4",
            }

        def fake_aliases(_ctx: object, kind: str = "image") -> list[dict[str, str]]:
            if kind != "video":
                return []
            return [
                {"alias": "Max SR2", "provider": "openrouter", "model": "bytedance/seedance-2.0"},
                {"alias": "Direct Video Alias", "provider": "openrouter", "model": "bytedance/direct-video-model"},
            ]

        def read_json(path: Path) -> dict[str, object]:
            return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

        def write_json(path: Path, payload: dict[str, object]) -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

        async def asgi_post_json(app: FastAPI, path: str, payload: dict[str, object]) -> tuple[int, str]:
            body = json.dumps(payload).encode("utf-8")
            sent_request = False
            response_status = 0
            response_body = bytearray()
            never_disconnect = asyncio.Event()

            async def receive() -> dict[str, object]:
                nonlocal sent_request
                if not sent_request:
                    sent_request = True
                    return {"type": "http.request", "body": body, "more_body": False}
                await never_disconnect.wait()
                return {"type": "http.disconnect"}

            async def send(message: dict[str, object]) -> None:
                nonlocal response_status
                if message["type"] == "http.response.start":
                    response_status = int(message.get("status") or 0)
                elif message["type"] == "http.response.body":
                    response_body.extend(message.get("body") or b"")

            await app(
                {
                    "type": "http",
                    "asgi": {"version": "3.0"},
                    "http_version": "1.1",
                    "method": "POST",
                    "scheme": "http",
                    "path": path,
                    "raw_path": path.encode("ascii"),
                    "query_string": b"",
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode("ascii"))],
                    "client": ("testclient", 50000),
                    "server": ("testserver", 80),
                },
                receive,
                send,
            )
            return response_status, response_body.decode("utf-8")

        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "SessionContext").mkdir(parents=True)
            (workspace / "SessionContext" / "VideoAPISettings.json").write_text(
                json.dumps(
                    {
                        "settings": {
                            "agentVideoAlias": "Direct Video Alias",
                            "provider": "",
                            "model": "",
                            "duration": 4,
                            "aspect": "9:16",
                            "count": 1,
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (workspace / "SessionContext" / "VideosAgentSettings.json").write_text(
                json.dumps(
                    {
                        "settings": {
                            "agentVideoAlias": "Max SR2",
                            "provider": "",
                            "model": "",
                            "duration": 4,
                            "aspect": "9:16",
                            "count": 1,
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            app = FastAPI()
            router = APIRouter()
            deps = SimpleNamespace(
                ctx=SimpleNamespace(),
                task_or_404=lambda task_id: {"id": task_id, "session_id": 77},
                workspace_for=lambda _task: workspace,
                read_json=read_json,
                write_json=write_json,
                text=text,
                add_event=add_event,
                generate_asset_library_video=generate_asset_library_video,
            )
            with (
                patch.object(asset_routes, "load_agent_model_aliases", fake_aliases),
                patch.object(asset_routes, "load_config", return_value={"providers": [], "agent_model_aliases": []}),
            ):
                asset_routes.register_asset_routes(router, deps)
                app.include_router(router)
                status, body = asyncio.run(asgi_post_json(
                    app,
                    "/api/koubo-storyboard/tasks/42/asset-library/video-api/generate/events",
                    {
                        "prompt": "生成一条科技感短视频",
                        "agentVideoAlias": "Max SR2",
                        "provider": "",
                        "model": "",
                        "duration": 4,
                        "aspect": "9:16",
                    },
                ))
                fallback_status, fallback_body = asyncio.run(asgi_post_json(
                    app,
                    "/api/koubo-storyboard/tasks/42/asset-library/video-api/generate/events",
                    {
                        "prompt": "生成另一条科技感短视频",
                        "provider": "",
                        "model": "",
                    },
                ))
                agent_fallback_status, agent_fallback_body = asyncio.run(asgi_post_json(
                    app,
                    "/api/koubo-storyboard/tasks/42/asset-library/video-api/generate/events",
                    {
                        "prompt": "由智能体生成一条科技感短视频",
                        "provider": "",
                        "model": "",
                        "settingsScope": "videos_agent",
                    },
                ))
                (workspace / "SessionContext" / "VideosAgentSettings.json").write_text(
                    json.dumps({"settings": {"agentVideoAlias": "Deleted Alias", "provider": "", "model": ""}}, ensure_ascii=False),
                    encoding="utf-8",
                )
                stale_saved_status, stale_saved_body = asyncio.run(asgi_post_json(
                    app,
                    "/api/koubo-storyboard/tasks/42/asset-library/video-api/generate/events",
                    {
                        "prompt": "保存的旧别名应回退 active config",
                        "provider": "",
                        "model": "",
                        "settingsScope": "videos_agent",
                    },
                ))
                (workspace / "SessionContext" / "VideoAPISettings.json").unlink()
                (workspace / "SessionContext" / "VideosAgentSettings.json").unlink()
                active_fallback_status, active_fallback_body = asyncio.run(asgi_post_json(
                    app,
                    "/api/koubo-storyboard/tasks/42/asset-library/video-api/generate/events",
                    {
                        "prompt": "不显式提交模型时交给服务层 active config 解析",
                        "provider": "",
                        "model": "",
                    },
                ))

        self.assertEqual(status, 200, body)
        self.assertEqual(fallback_status, 200, fallback_body)
        self.assertEqual(agent_fallback_status, 200, agent_fallback_body)
        self.assertEqual(stale_saved_status, 200, stale_saved_body)
        self.assertEqual(active_fallback_status, 200, active_fallback_body)
        active_frames = [
            json.loads(line.removeprefix("data: "))
            for line in active_fallback_body.splitlines()
            if line.startswith("data: ")
        ]
        self.assertEqual(active_frames[0]["agentVideoAlias"], "")
        self.assertEqual(active_frames[0]["provider"], "")
        self.assertEqual(active_frames[0]["model"], "")
        self.assertNotIn("openrouter", fallback_body)
        self.assertNotIn("seedance", fallback_body)
        fallback_frames = [
            json.loads(line.removeprefix("data: "))
            for line in fallback_body.splitlines()
            if line.startswith("data: ")
        ]
        self.assertEqual(fallback_frames[0]["agentVideoAlias"], "Direct Video Alias")
        self.assertEqual(fallback_frames[0]["provider"], "")
        self.assertEqual(fallback_frames[0]["model"], "")
        agent_frames = [
            json.loads(line.removeprefix("data: "))
            for line in agent_fallback_body.splitlines()
            if line.startswith("data: ")
        ]
        self.assertEqual(agent_frames[0]["agentVideoAlias"], "Max SR2")
        self.assertEqual(agent_frames[0]["provider"], "")
        self.assertEqual(agent_frames[0]["model"], "")
        self.assertNotIn("openrouter", agent_fallback_body)
        self.assertNotIn("seedance", agent_fallback_body)
        self.assertEqual(generated_payloads[2]["agentVideoAlias"], "Max SR2")
        self.assertEqual(generated_payloads[2]["provider"], "")
        self.assertEqual(generated_payloads[2]["model"], "")
        stale_saved_frames = [
            json.loads(line.removeprefix("data: "))
            for line in stale_saved_body.splitlines()
            if line.startswith("data: ")
        ]
        self.assertEqual(stale_saved_frames[0]["agentVideoAlias"], "")
        self.assertEqual(stale_saved_frames[0]["provider"], "")
        self.assertEqual(stale_saved_frames[0]["model"], "")
        self.assertNotIn("agentVideoAlias", generated_payloads[3])
        self.assertEqual(generated_payloads[3]["provider"], "")
        self.assertEqual(generated_payloads[3]["model"], "")
        self.assertEqual(generated_payloads[4]["provider"], "")
        self.assertEqual(generated_payloads[4]["model"], "")
        self.assertNotIn("agentVideoAlias", generated_payloads[4])
        frames = [
            json.loads(line.removeprefix("data: "))
            for line in body.splitlines()
            if line.startswith("data: ")
        ]
        self.assertGreaterEqual(len(frames), 2)
        started = frames[0]
        self.assertEqual(started["type"], "started")
        self.assertEqual(started["agentVideoAlias"], "Max SR2")
        self.assertEqual(started["provider"], "")
        self.assertEqual(started["model"], "")
        self.assertNotIn("openrouter", body)
        self.assertNotIn("seedance", body)
        self.assertEqual(generated_payloads[0]["agentVideoAlias"], "Max SR2")
        self.assertEqual(generated_payloads[0]["provider"], "")
        self.assertEqual(generated_payloads[0]["model"], "")
        self.assertEqual(events[0]["payload"]["agentVideoAlias"], "Max SR2")
        self.assertEqual(events[0]["payload"]["provider"], "")
        self.assertEqual(events[0]["payload"]["model"], "")

    def test_frontend_asset_agent_chat_dedupes_duplicate_submits(self) -> None:
        panel_source = AGENT_PANEL_PATH.read_text(encoding="utf-8")
        send_section = panel_source.split("const sendAgentChatPayload = async", 1)[1].split("const sendAgentChat = async", 1)[0]

        for token in (
            'let agentChatSubmitInFlightKey = "";',
            "const agentChatSubmitKey = ",
            "const submittedReferences = referencePayloadItems();",
            "const submitKey = agentChatSubmitKey(value, options, submittedReferences);",
            "if (agentChatSubmitInFlightKey === submitKey) return;",
            "agentChatSubmitInFlightKey = submitKey;",
            "referenceAttachments: displayReferenceItemsForPayload(submittedReferences)",
            "reference_images: submittedReferences",
            "finally",
            'if (agentChatSubmitInFlightKey === submitKey) agentChatSubmitInFlightKey = "";',
        ):
            self.assertIn(token, panel_source if token.startswith(("let ", "const agentChatSubmitKey")) else send_section)

    def test_frontend_image_agent_refresh_restores_result_cards_from_assets(self) -> None:
        panel_source = AGENT_PANEL_PATH.read_text(encoding="utf-8")

        for token in (
            "agentAssetGenerationId",
            "agentAssetMessageId",
            "agentAssetSessionId",
            "collectAgentImageResultGroups",
            "mergeAgentImageResultIntoMessage",
            "groupsByMessageId",
            "duplicateSyntheticIds",
            "currentIds.has(group.messageId)",
            "groupsByMessageId.get(group.messageId)?.key === group.key",
            "if (duplicateSyntheticIds.has(id))",
            "usedGroupKeys",
            "agent-image-${group.key}",
            "source !== \"agent_generated\"",
            "origin.tool !== \"upload_asset_library_agent\"",
            "!generationId && !messageId && !assetSessionId",
            "if (sessionId && assetSessionId && assetSessionId !== sessionId) continue",
            "if (!text(message.text) && !parsedText) next.text = patch.text",
            "const visible = Boolean(message.imagePlaceholder || message.imageUrl || parsed.text || parsed.thinking || candidates.length || references.length)",
            "<Show when={!message.imageUrl}>",
            "{renderDebugDetails(parsed)}",
        ):
            self.assertIn(token, panel_source)

    def test_frontend_image_workspace_history_does_not_overwrite_loaded_records_with_empty_snapshot(self) -> None:
        panel_source = AGENT_PANEL_PATH.read_text(encoding="utf-8")
        queue_section = panel_source.split("const queueDirectImageHistorySave = ", 1)[1].split("const initializeDirectImageHistory = ", 1)[0]
        initialize_section = panel_source.split("const initializeDirectImageHistory = ", 1)[1].split("const firstIndexOfAny = ", 1)[0]

        self.assertLess(
            queue_section.index("if (!directHistoryReady() || directHistoryInitializing) {"),
            queue_section.index("directHistorySaveSnapshot = items;"),
        )
        self.assertIn("directHistoryInitializing", queue_section)
        self.assertIn("if (directImageHistoryMessagesForSave(items).length) directHistorySaveSnapshot = items;", queue_section)
        self.assertIn("queueDirectImageHistorySave(next);", panel_source)
        self.assertIn("const userMessage = {", panel_source)
        self.assertIn("void saveDirectImageHistorySnapshot([...messages().filter((message) => message.id !== userMessage.id && message.id !== messageId), userMessage, { ...assistantMessage, ...completedPatch }]);", panel_source)
        self.assertIn("directHistoryInitializing = true;", initialize_section)
        self.assertIn("let nextMessages = null;", initialize_section)
        self.assertIn("nextMessages = normalizeChatMessages(storedMessages);", initialize_section)
        self.assertIn("directHistorySaveSnapshot = null;", initialize_section)
        self.assertIn("setDirectHistoryReady(false);", initialize_section)
        self.assertIn("setDirectHistoryReady(true);", initialize_section)
        self.assertIn("queueDirectImageHistorySave(nextMessages || directHistorySaveSnapshot || messages(), 0);", initialize_section)
        self.assertNotIn("flushDirectImageHistorySave();", initialize_section)

    def test_max_sr2_video_references_are_wired_through_frontend_and_backend(self) -> None:
        overlay_source = OVERLAY_PATH.read_text(encoding="utf-8")
        panel_source = VIDEO_AGENT_PANEL_PATH.read_text(encoding="utf-8")
        capability_source = VIDEO_MODEL_CAPABILITIES_PATH.read_text(encoding="utf-8")
        services_source = ASSET_VIDEO_GENERATION_SERVICES_PATH.read_text(encoding="utf-8")
        routes_source = (REPO_ROOT / "backend" / "opcrew_backend" / "koubo" / "koubo_storyboard" / "agent_chat_routes.py").read_text(encoding="utf-8")
        prompt_source = (REPO_ROOT / "backend" / "opcrew_backend" / "koubo" / "koubo_storyboard" / "agent_chat_services.py").read_text(encoding="utf-8")
        model_config_source = MEDIA_MODEL_CONFIG_PATH.read_text(encoding="utf-8")

        for token in (
            "selectedVideoReferenceAssets",
            "splitVideoReferencePayload",
            "reference_audios: referencePayload.reference_audios",
            "reference_videos: referencePayload.reference_videos",
            "reference_mode: referenceModeForVideoGeneration",
        ):
            self.assertIn(token, overlay_source)

        for token in (
            "referenceAudios",
            "referenceVideos",
            "resolveVideoModelCapability",
            "capability.referenceMode",
            "selected_reference_audios",
            "selected_reference_videos",
        ):
            self.assertIn(token, panel_source)

        for token in (
            "aliasKey === \"maxsr2\"",
            "referenceMode = \"input_references\"",
            "max: 8",
            "totalMax",
        ):
            self.assertIn(token, capability_source)

        for token in (
            "validate_video_reference_media",
            "refs[:8]",
            "reference_values_for_kind(payload, \"audio\")",
            "reference_values_for_kind(payload, \"video\")",
            "reference_audio_paths",
            "reference_video_paths",
            "unique_reference_value_count",
            "Max SR2 supports at most 8 image references",
            "\"input_references\"",
        ):
            self.assertIn(token, services_source)

        for token in (
            "reference_audios",
            "reference_videos",
            "normalize_video_reference_mode",
            "normalize_reference_path_list(payload.get(\"reference_images\"), 8)",
        ):
            self.assertIn(token, routes_source)

        for token in (
            "reference_audios",
            "reference_videos",
            "reference_mode",
            "input_references",
        ):
            self.assertIn(token, prompt_source)

        for token in (
            "reference_audios",
            "reference_videos",
            "input_references",
            "bytedance/seedance-2.0",
        ):
            self.assertIn(token, model_config_source)

    def test_max_si2_wr27_hr10_video_aliases_are_wired_through_frontend_and_backend(self) -> None:
        overlay_source = OVERLAY_PATH.read_text(encoding="utf-8")
        panel_source = VIDEO_AGENT_PANEL_PATH.read_text(encoding="utf-8")
        capability_source = VIDEO_MODEL_CAPABILITIES_PATH.read_text(encoding="utf-8")
        services_source = ASSET_VIDEO_GENERATION_SERVICES_PATH.read_text(encoding="utf-8")
        agent_routes_source = AGENT_CHAT_ROUTES_PATH.read_text(encoding="utf-8")
        agent_prompt_source = (REPO_ROOT / "backend" / "opcrew_backend" / "koubo" / "koubo_storyboard" / "agent_chat_services.py").read_text(encoding="utf-8")

        for token in (
            "hasExplicitReferenceMode",
            "referenceModeForVideoGeneration",
        ):
            self.assertIn(token, overlay_source)

        for token in (
            "resolveVideoModelCapability",
            "validateVideoGenerationInputs",
            "checked.capability.referenceMode",
        ):
            self.assertIn(token, panel_source)

        for token in (
            "aliasKey === \"maxsi2\"",
            "referenceMode = \"first_frame\"",
            "aliasKey === \"maxwr27\"",
            "aliasKey === \"maxhr10\"",
        ):
            self.assertIn(token, capability_source)

        for token in (
            "agent_video_alias",
            "maxsi2",
            "maxwr27",
            "maxhr10",
            "run_wan_rtv_asset_video",
            "run_chanjing_happyhorse_asset_video",
            "WAN_R2V_MODEL",
            "HAPPYHORSE_R2V_MODEL",
            "WAN_R2V_REFERENCE_TOTAL_LIMIT",
            "len(reference_paths) > 1",
            "total_reference_count = reference_image_input_count + reference_video_input_count",
        ):
            self.assertIn(token, services_source)

        for token in (
            "Max SI2",
            "Max WR2.7",
            "Max HR1.0",
            "reference_videos",
            "reference_images",
        ):
            self.assertIn(token, agent_prompt_source)
        self.assertIn("aspect 只能是 9:16 或 16:9", agent_prompt_source)
        self.assertIn('if aspect not in {"9:16", "16:9"}:', agent_routes_source)

    def test_xai_image_to_video_reframes_mismatched_reference_aspect_before_generation(self) -> None:
        services_source = ASSET_VIDEO_GENERATION_SERVICES_PATH.read_text(encoding="utf-8")

        for token in (
            "VIDEO_IMAGE_REFRAME_SOURCE_RATIO_TOLERANCE",
            "image_reference_matches_video_aspect",
            "video_reference_reframe_prompt",
            "prepare_xai_image_to_video_reference",
            "sc.load_reference_image_config(\"\", \"\", sc=sc)",
            "sc.generate_image_bytes(image_config, reframe_prompt, [source_path], target_size, aspect, sc=sc)",
            "koubo_storyboard.asset_library_agent.video.portrait_reframe.started",
            "koubo_storyboard.asset_library_agent.video.portrait_reframe.completed",
            "provider_meta[\"portrait_reframe\"] = portrait_reframe",
            "inline = image_inline_payload(xai_image)",
        ):
            self.assertIn(token, services_source)
        self.assertIn("do not stretch, squeeze, or warp", services_source)

    def test_asset_video_prompt_builder_routes_seedance_to_openrouter_template(self) -> None:
        source = ASSET_ROUTES_PATH.read_text(encoding="utf-8")

        self.assertIn('if provider_value in {"seedance", "bytedance", "volcengine", "doubao"} or "seedance" in model_value:', source)
        self.assertIn('return "openrouter"', source)
        self.assertIn('"filename": "Video_OpenRouter.md"', source)

    def test_video_agent_design_doc_contains_test_plan_and_presentation_rules(self) -> None:
        source = VIDEO_AGENT_DESIGN_DOC_PATH.read_text(encoding="utf-8")
        for token in (
            "## 7. Test Plan",
            "Provider API contract",
            "Direct Videos workspace",
            "Videos-Agent workspace",
            "Host/Product references",
            "Standard Agent conversation presentation",
            "UI verification",
            "Video_Grok.py",
            "Video_Gemini.py",
            "<VIDEO_GENERATION_REQUEST>",
            "raw OpenCode protocol text",
        ):
            self.assertIn(token, source)

    def test_asset_library_image_model_catalog_matches_session_provider_rules(self) -> None:
        source = MEDIA_MODEL_CONFIG_PATH.read_text(encoding="utf-8")
        image_section = source.split('"image": [', 1)[1].split('],\n        "video"', 1)[0]
        openai_section = image_section.split('"provider": "openai"', 1)[1].split('"provider": "xai"', 1)[0]
        gemini_section = image_section.split('"provider": "gemini"', 1)[1]
        xai_section = image_section.split('"provider": "xai"', 1)[1].split('"provider": "gemini"', 1)[0]

        self.assertIn('model_option("gpt-image-2")', openai_section)
        self.assertIn('model_option("gpt-image-1.5")', openai_section)
        self.assertNotIn('model_option("gpt-image-1")', openai_section)
        self.assertNotIn('model_option("gpt-image-1-mini")', openai_section)
        for token in ('model_option("gemini-3.1-flash-image")', 'model_option("gemini-3-pro-image")', 'model_option("gemini-2.5-flash-image")'):
            self.assertIn(token, gemini_section)
        for token in ('model_option("grok-imagine-image-quality"', 'model_option("grok-imagine-image"'):
            self.assertIn(token, xai_section)

    def test_image_prompt_templates_do_not_render_dialogue_text_as_visual_text(self) -> None:
        for name in ("Image_Grok.md", "Image_GPT.md", "Image_Gemini.md"):
            source = (IMAGE_TEMPLATE_DIR / name).read_text(encoding="utf-8")
            self.assertNotIn("{{dialogue_text}}", source)
            self.assertIn("Spoken-dialogue context: semantic guidance only.", source)
            self.assertIn("Do not render dialogue words", source)

    def test_image_prompt_templates_preserve_product_geometry_for_cutaways(self) -> None:
        for name in ("Image_Grok.md", "Image_GPT.md", "Image_Gemini.md"):
            source = (IMAGE_TEMPLATE_DIR / name).read_text(encoding="utf-8")
            self.assertIn("PRODUCT_REFERENCE physical geometry outranks TARGET_FRAME composition", source)
            self.assertIn("real package aspect ratio", source)
            self.assertIn("vertically stretched package", source)
            self.assertIn("neutral tabletop/background space", source)


if __name__ == "__main__":
    unittest.main()
