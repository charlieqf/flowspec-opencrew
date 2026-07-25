from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
MODEL_POLICY_PATH = REPO_ROOT / "backend" / "opcrew_backend" / "model_policy.py"
COMMON_PATH = REPO_ROOT / "backend" / "opcrew_backend" / "koubo" / "koubo_storyboard" / "agent_chat_common.py"
ROUTES_PATH = REPO_ROOT / "backend" / "opcrew_backend" / "koubo" / "koubo_storyboard" / "agent_chat_routes.py"
SERVICES_PATH = REPO_ROOT / "backend" / "opcrew_backend" / "koubo" / "koubo_storyboard" / "agent_chat_services.py"
SERVICE_BOOTSTRAP_PATH = REPO_ROOT / "backend" / "opcrew_backend" / "koubo" / "koubo_storyboard" / "services.py"
ASSET_VIDEO_GENERATION_SERVICES_PATH = REPO_ROOT / "backend" / "opcrew_backend" / "koubo" / "koubo_storyboard" / "asset_video_generation_services.py"
ASSET_ROUTES_PATH = REPO_ROOT / "backend" / "opcrew_backend" / "koubo" / "koubo_storyboard" / "asset_routes.py"
ROUTER_PATH = REPO_ROOT / "backend" / "opcrew_backend" / "koubo" / "koubo_storyboard" / "router.py"
KOUBO_API_PATH = REPO_ROOT / "frontend" / "src" / "modules" / "koubo" / "KouboStoryBoard" / "kouboStoryboardApi.js"
KOUBO_MODULE_PATH = REPO_ROOT / "frontend" / "src" / "modules" / "koubo" / "KouboStoryBoardModule.jsx"
AGENT_CHAT_JS_PATH = REPO_ROOT / "frontend" / "src" / "modules" / "koubo" / "KouboStoryBoard" / "kouboAgentChat.js"
AGENT_DRAWER_PATH = REPO_ROOT / "frontend" / "src" / "modules" / "koubo" / "KouboStoryBoard" / "components" / "KouboAgentDrawer.jsx"
UPLOAD_OVERLAY_PATH = REPO_ROOT / "frontend" / "src" / "modules" / "koubo" / "UploadAssetLibrary" / "UploadAssetLibraryOverlay.jsx"
UPLOAD_SIDEBAR_PATH = REPO_ROOT / "frontend" / "src" / "modules" / "koubo" / "UploadAssetLibrary" / "components" / "LibrarySidebar.jsx"
IMAGE_MODAL_PATH = REPO_ROOT / "frontend" / "src" / "modules" / "koubo" / "KouboStoryBoard" / "components" / "KouboImagePlanModal.jsx"
VIDEO_MODAL_PATH = REPO_ROOT / "frontend" / "src" / "modules" / "koubo" / "KouboStoryBoard" / "components" / "KouboVideoPlanModal.jsx"
COMPOSER_MODAL_PATH = REPO_ROOT / "frontend" / "src" / "modules" / "koubo" / "KouboStoryBoard" / "components" / "KouboComposerModal.jsx"


class KouboStoryboardAgentsChatContractTest(unittest.TestCase):
    def test_common_security_module_is_shared_by_asset_and_new_agents(self) -> None:
        common = COMMON_PATH.read_text(encoding="utf-8")
        asset_routes = ASSET_ROUTES_PATH.read_text(encoding="utf-8")

        for token in (
            "KOUBO_AGENT_CHAT_DISABLED_TOOLS",
            '"bash": False',
            '"read": False',
            '"write": False',
            '"websearch": False',
            "KOUBO_AGENT_CHAT_MESSAGE_LIMIT = 500",
            "def sanitize_opencode_event",
            "def opencode_event_has_tool_use",
            "def safe_opencode_message",
        ):
            self.assertIn(token, common)

        for token in (
            "from .agent_chat_common import",
            "ASSET_AGENT_CHAT_DISABLED_TOOLS = KOUBO_AGENT_CHAT_DISABLED_TOOLS",
            "ASSET_AGENT_CHAT_MESSAGE_LIMIT = KOUBO_AGENT_CHAT_MESSAGE_LIMIT",
            "tools=ASSET_AGENT_CHAT_DISABLED_TOOLS",
        ):
            self.assertIn(token, asset_routes)

        self.assertEqual(asset_routes.count("def sanitize_opencode_event"), 0)
        self.assertEqual(asset_routes.count("def opencode_event_has_tool_use"), 0)

    def test_backend_routes_services_and_session_files_are_wired(self) -> None:
        routes = ROUTES_PATH.read_text(encoding="utf-8")
        services = SERVICES_PATH.read_text(encoding="utf-8")
        router = ROUTER_PATH.read_text(encoding="utf-8")

        for token in (
            'AGENT_CHAT_ROOT_REL = "SessionContext/AgentChats"',
            'AGENT_CHAT_SCHEMA = "koubo_storyboard_agent_chat_0.1"',
            '"storyboard_edit"',
            '"image_plan"',
            '"video_plan"',
            '"composer"',
            "/agents/{agent_key}/chat/ensure-session",
            "/agents/{agent_key}/chat/messages",
            "/agents/{agent_key}/chat/message",
            "/agents/{agent_key}/chat/events",
            "/agents/{agent_key}/chat/abort",
            "AGENT_CHAT_MODEL_ROLE = AUTH_ROLE_USER",
            "resolve_model(session_row, provider, model_id, AGENT_CHAT_MODEL_ROLE, surface, sc=deps)",
            "mask_model_fields_for_role(deps.ctx, AGENT_CHAT_MODEL_ROLE, surface, model)",
            "tools=KOUBO_AGENT_CHAT_DISABLED_TOOLS",
            "opencode_event_has_tool_use",
            "koubo_storyboard.agent_chat.tool_blocked",
            "sanitize_opencode_event",
            "safe_opencode_message",
        ):
            self.assertIn(token, routes)

        for token in (
            "def agent_chat_context",
            "def agent_chat_system_prompt",
            "<STORYBOARD_EDIT_CANDIDATE>",
            "<IMAGE_PLAN_CANDIDATE>",
            "<IMAGE_PLAN_ACTION>",
            "<VIDEO_PLAN_ACTION>",
            "<COMPOSER_DIAGNOSIS>",
            "VIDEO_PLAN_SETTINGS_REL",
            "video_plan_settings({\"settings\": settings})",
        ):
            self.assertIn(token, services)

        self.assertIn("register_agent_chat_routes(router, deps)", router)

    def test_new_model_surfaces_are_explicitly_registered(self) -> None:
        source = MODEL_POLICY_PATH.read_text(encoding="utf-8")
        for token in (
            'SURFACE_KOUBO_STORYBOARD_EDIT_AGENT_CHAT = "koubo.storyboard.edit_agent_chat"',
            'SURFACE_KOUBO_IMAGE_PLAN_AGENT_CHAT = "koubo.image_plan.agent_chat"',
            'SURFACE_KOUBO_VIDEO_PLAN_AGENT_CHAT = "koubo.video_plan.agent_chat"',
            'SURFACE_KOUBO_COMPOSER_AGENT_CHAT = "koubo.composer.agent_chat"',
            'DEFAULT_USER_MODEL_POLICY["surfaces"][_surface]',
            "SURFACE_KOUBO_ASSET_AGENT_CHAT",
        ):
            self.assertIn(token, source)

        self.assertIn("for _surface in (", source)
        self.assertIn("copy.deepcopy(", source)
        self.assertIn('DEFAULT_USER_MODEL_POLICY["surfaces"][SURFACE_KOUBO_ASSET_AGENT_CHAT]', source)

    def test_frontend_agent_api_drawer_and_entry_points_are_wired(self) -> None:
        api = KOUBO_API_PATH.read_text(encoding="utf-8")
        module = KOUBO_MODULE_PATH.read_text(encoding="utf-8")
        drawer = AGENT_DRAWER_PATH.read_text(encoding="utf-8")
        agent_js = AGENT_CHAT_JS_PATH.read_text(encoding="utf-8")
        image_modal = IMAGE_MODAL_PATH.read_text(encoding="utf-8")
        video_modal = VIDEO_MODAL_PATH.read_text(encoding="utf-8")
        composer_modal = COMPOSER_MODAL_PATH.read_text(encoding="utf-8")

        for token in (
            "agentChatEnsureSession",
            "agentChatMessages",
            "agentChatSendMessage",
            "agentChatAbort",
            "agentChatEventsUrl",
        ):
            self.assertIn(token, api)

        for token in (
            "EventSource",
            "reduceAgentChatEvent",
            "extractAgentCandidates",
            "renderCandidate",
            "buildClientContext",
            "scheduleHistoryFallback",
            "completedAssistantAfter",
        ):
            self.assertIn(token, drawer)

        for token in (
            "STORYBOARD_EDIT_CANDIDATE",
            "IMAGE_PLAN_CANDIDATE",
            "VIDEO_PLAN_ACTION",
            "COMPOSER_DIAGNOSIS",
            "applyStoryboardEditCandidate",
        ):
            self.assertIn(token, agent_js)
        self.assertNotIn("set_talking_head", agent_js)

        self.assertIn('agentKey="storyboard_edit"', module)
        self.assertIn("openStoryboardAgent", module)
        self.assertIn('agentKey="image_plan"', image_modal)
        self.assertIn("fillPromptCandidate", image_modal)
        self.assertIn('agentKey="video_plan"', video_modal)
        self.assertIn('openVideoPlan?.({ ...target, force: true, action_source: "agent_candidate" })', video_modal)
        self.assertIn('agentKey="composer"', composer_modal)
        self.assertIn("generate_task_video_plan", composer_modal)

    def test_upload_asset_video_agent_generation_bridge_is_wired(self) -> None:
        routes = ROUTES_PATH.read_text(encoding="utf-8")
        services = SERVICES_PATH.read_text(encoding="utf-8")
        bootstrap = SERVICE_BOOTSTRAP_PATH.read_text(encoding="utf-8")
        video_services = ASSET_VIDEO_GENERATION_SERVICES_PATH.read_text(encoding="utf-8")
        drawer = AGENT_DRAWER_PATH.read_text(encoding="utf-8")
        agent_js = AGENT_CHAT_JS_PATH.read_text(encoding="utf-8")
        overlay = UPLOAD_OVERLAY_PATH.read_text(encoding="utf-8")
        sidebar = UPLOAD_SIDEBAR_PATH.read_text(encoding="utf-8")

        for token in (
            "VIDEO_GENERATION_REQUEST",
            "asset_video_generation_keys",
            "claim_agent_video_generation_key",
            "extract_agent_video_generation_requests",
            "generate_asset_library_video",
            "asset_agent.video_generation.started",
            "asset_agent.video_generation.completed",
            "asset_agent.video_generation.failed",
            "enqueue_existing_agent_video_generations",
        ):
            self.assertIn(token, routes)

        for token in (
            "register_asset_video_generation_services(ns)",
            "asset_video_generation_services",
        ):
            self.assertIn(token, bootstrap)

        for token in (
            "def load_video_config",
            "def generate_asset_library_video",
            "ASSET_VIDEOS_REL",
            "sc.upsert_asset_manifest_item(workspace, asset, sc=sc)",
            "koubo_storyboard.asset_library_agent.video.generated",
            'provider in {"bytedance", "seedance", "volcengine", "ark"}',
            "/contents/generations/tasks",
            '"role": "first_frame"',
            "Seedance response did not include task id",
        ):
            self.assertIn(token, video_services)

        self.assertIn("VIDEO_GENERATION_REQUEST", services)
        self.assertIn("HIDDEN_TAGS = [\"VIDEO_GENERATION_REQUEST\"]", agent_js)

        for token in (
            "handleAssetVideoEvent",
            "asset_agent.video_generation.completed",
            "kbsp-agent-video",
            "props.mode === \"panel\"",
            "generatedAssets",
        ):
            self.assertIn(token, drawer)

        for token in (
            "视频智能体",
            "showVideoAgentPanel",
            'agentKey="asset_video"',
            "onAgentVideoGenerationEvent={handleAgentVideoGenerationEvent}",
            "buildVideoAgentContext",
        ):
            self.assertIn(token, overlay + sidebar)


if __name__ == "__main__":
    unittest.main()
