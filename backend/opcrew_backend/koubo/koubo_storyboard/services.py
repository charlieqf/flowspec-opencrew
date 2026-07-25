from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass, field
from typing import Any

from opcrew_backend.context import AppContext

from .io_utils import read_json, safe_workspace_rel, write_json
from .runtime import KouboStoryboardRuntime, analysis_tool_env
from .text_utils import redact_payload, redact_secret_text
from .builder_state_services import register_builder_state_services
from .provider_services import register_provider_services
from .media_tts_provider_services import register_media_tts_provider_services
from .asset_services import register_asset_services
from .value_services import register_value_services
from .storyboard_plan_services import register_storyboard_plan_services
from .tts_workflow_services import register_tts_workflow_services
from .video_plan_services import register_video_plan_services
from .composer_services import register_composer_services
from .tool_runner_services import register_tool_runner_services
from .host_product_services import register_host_product_services
from .agent_chat_services import register_agent_chat_services
from .clean_image_services import register_clean_image_services
from .asset_video_generation_services import register_asset_video_generation_services
from .asset_search_services import register_asset_search_services


@dataclass
class StoryboardContext:
    ctx: AppContext
    repo: Any
    video_plan_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    video_plan_execution_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    image_plan_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    image_plan_execution_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    video_only_plan_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    video_only_plan_execution_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    composer_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    video_plan_execution_jobs: dict[Any, Any] = field(default_factory=dict)
    image_plan_execution_jobs: dict[Any, Any] = field(default_factory=dict)
    video_only_plan_execution_jobs: dict[Any, Any] = field(default_factory=dict)
    composer_execution_jobs: dict[Any, Any] = field(default_factory=dict)
    tts_output_lock_guard: Any = field(default_factory=threading.Lock)
    tts_output_locks: dict[str, Any] = field(default_factory=dict)
    tts_output_generations: dict[str, dict[str, str]] = field(default_factory=dict)
    read_json: Any = read_json
    write_json: Any = write_json
    safe_workspace_rel: Any = safe_workspace_rel
    analysis_tool_env: Any = analysis_tool_env
    redact_payload: Any = redact_payload
    redact_secret_text: Any = redact_secret_text
    runtime: KouboStoryboardRuntime = field(init=False)
    task_or_404: Any = field(init=False)
    workspace_for: Any = field(init=False)
    safe_session: Any = field(init=False)
    add_event: Any = field(init=False)

    def __post_init__(self) -> None:
        self.runtime = KouboStoryboardRuntime(self.ctx, self.repo)
        self.task_or_404 = self.runtime.task_or_404
        self.workspace_for = self.runtime.workspace_for
        self.safe_session = self.runtime.safe_session
        self.add_event = self.runtime.add_event


def _ensure_event_loop() -> None:
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())


def build_koubo_storyboard_services(ctx: AppContext, repo: Any) -> StoryboardContext:
    _ensure_event_loop()
    ns = StoryboardContext(ctx=ctx, repo=repo)
    shared_video_jobs = getattr(ctx, "koubo_video_plan_execution_jobs", None)
    if not isinstance(shared_video_jobs, dict):
        shared_video_jobs = {}
        setattr(ctx, "koubo_video_plan_execution_jobs", shared_video_jobs)
    ns.video_plan_execution_jobs = shared_video_jobs
    register_builder_state_services(ns)
    register_provider_services(ns)
    register_media_tts_provider_services(ns)
    register_asset_services(ns)
    register_value_services(ns)
    register_storyboard_plan_services(ns)
    register_tts_workflow_services(ns)
    register_video_plan_services(ns)
    register_composer_services(ns)
    register_tool_runner_services(ns)
    register_host_product_services(ns)
    register_asset_video_generation_services(ns)
    register_asset_search_services(ns)
    register_agent_chat_services(ns)
    register_clean_image_services(ns)
    ns.start_gemini_omni_recovery_worker(sc=ns)
    return ns
