from __future__ import annotations

from fastapi import APIRouter

from opcrew_backend.context import AppContext
from ..repository import OpenClipRepository


from .services import build_koubo_storyboard_services
from .asset_routes import register_asset_routes
from .asset_search_routes import register_asset_search_routes
from .asset_digital_human_routes import register_asset_digital_human_routes
from .agent_chat_routes import register_agent_chat_routes
from .clean_image_routes import register_clean_image_routes
from .composer_routes import register_composer_routes
from .hyperframe_template_routes import register_hyperframe_template_routes
from .host_product_routes import register_host_product_routes
from .image_plan_routes import register_image_plan_routes
from .task_routes import register_task_routes
from .tts_routes import register_tts_routes
from .video_only_plan_routes import register_video_only_plan_routes
from .video_plan_routes import register_video_plan_routes


def build_koubo_storyboard_router(ctx: AppContext) -> APIRouter:
    router = APIRouter()
    repo = OpenClipRepository(ctx.engine)
    deps = build_koubo_storyboard_services(ctx, repo)
    # Reuse the same provider/search/import context from the media-library
    # editor instead of constructing a second set of provider services.
    ctx.koubo_storyboard_services = deps
    register_task_routes(router, deps)
    register_video_plan_routes(router, deps)
    register_image_plan_routes(router, deps)
    register_video_only_plan_routes(router, deps)
    register_composer_routes(router, deps)
    register_hyperframe_template_routes(router, deps)
    register_host_product_routes(router, deps)
    register_clean_image_routes(router, deps)
    register_asset_routes(router, deps)
    register_asset_search_routes(router, deps)
    register_asset_digital_human_routes(router, deps)
    register_agent_chat_routes(router, deps)
    register_tts_routes(router, deps)

    # Testability handle: Phase R routes hold deps in closures, so contract
    # tests patch the mutable context here instead of module globals.
    router.storyboard_context = deps

    return router
