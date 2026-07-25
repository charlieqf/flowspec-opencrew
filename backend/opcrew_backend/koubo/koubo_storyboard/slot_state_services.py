from __future__ import annotations

from dataclasses import dataclass
from typing import Any


GREEN = "green"
WHITE = "white"
GRAY = "gray"
YELLOW = "yellow"
RED = "red"

DONE = "done"
PENDING = "pending"
DISABLED = "disabled"
RUNNING = "running"
FAILED = "failed"

BLOCKED_WAITING_INPUT = "blocked_waiting_input"
SKIPPED_CONSUMED_BY_DOWNSTREAM = "skipped_consumed_by_downstream"
FILE_EXISTS_UNBOUND = "file_exists_unbound"


@dataclass(frozen=True)
class SlotInputs:
    audio_exists: bool = False
    source_exists: bool = False
    image_exists: bool = False
    raw_exists: bool = False
    final_exists: bool = False
    image_prompt_exists: bool = False
    video_prompt_exists: bool = False
    final_bound: bool = False
    binding_missing: bool = False
    binding_stale: bool = False


def _bool(value: Any) -> bool:
    return bool(value)


def slot_inputs_from_vector(
    slots: tuple[int, int, int, int, int] | list[int],
    *,
    image_prompt_exists: bool = False,
    video_prompt_exists: bool = False,
    final_bound: bool = False,
    binding_missing: bool = False,
    binding_stale: bool = False,
) -> SlotInputs:
    if len(slots) != 5:
        raise ValueError("slots must contain exactly 5 values: [audio, source, image, raw, final]")
    audio, source, image, raw, final = [_bool(item) for item in slots]
    return SlotInputs(
        audio_exists=audio,
        source_exists=source,
        image_exists=image,
        raw_exists=raw,
        final_exists=final,
        image_prompt_exists=_bool(image_prompt_exists),
        video_prompt_exists=_bool(video_prompt_exists),
        final_bound=_bool(final_bound),
        binding_missing=_bool(binding_missing),
        binding_stale=_bool(binding_stale),
    )


def _state(
    color: str,
    reason: str,
    *,
    file_exists: bool = False,
    binding_consistency: str = "not_applicable",
    title: str = "",
) -> dict[str, Any]:
    ui_tone = {
        GREEN: DONE,
        WHITE: PENDING,
        GRAY: DISABLED,
        YELLOW: RUNNING,
        RED: FAILED,
    }[color]
    return {
        "color": color,
        "tone": ui_tone,
        "ui_tone": ui_tone,
        "color_zh": {
            GREEN: "绿",
            WHITE: "白",
            GRAY: "灰",
            YELLOW: "黄",
            RED: "红",
        }[color],
        "reason": reason,
        "file_exists": file_exists,
        "binding_consistency": binding_consistency,
        "title": title,
    }


def _apply_execution(slot: dict[str, Any], execution_status: str = "") -> dict[str, Any]:
    if slot.get("file_exists"):
        return slot
    status = str(execution_status or "").strip().lower()
    if status in {"queued", "running", "running_generate", "running_copy", "processing", "executing"}:
        return _state(YELLOW, "execution_running", title="正在执行")
    if status == "failed":
        return _state(RED, "execution_failed", title="执行失败")
    return slot


def _done(reason: str, *, binding_consistency: str = "consistent", title: str = "已完成") -> dict[str, Any]:
    return _state(GREEN, reason, file_exists=True, binding_consistency=binding_consistency, title=title)


def _pending_binding(title: str = "Final Video 已存在，待绑定 StoryBoard") -> dict[str, Any]:
    return _state(GREEN, FILE_EXISTS_UNBOUND, file_exists=True, binding_consistency=FILE_EXISTS_UNBOUND, title=title)


def _pending(reason: str, title: str = "等待执行") -> dict[str, Any]:
    return _state(WHITE, reason, title=title)


def _blocked(reason: str = BLOCKED_WAITING_INPUT, title: str = "条件不满足") -> dict[str, Any]:
    return _state(GRAY, reason, title=title)


def _skipped(reason: str = SKIPPED_CONSUMED_BY_DOWNSTREAM, title: str = "无需执行") -> dict[str, Any]:
    return _state(GRAY, reason, title=title)


def _file_binding_consistency(inputs: SlotInputs) -> str:
    if inputs.binding_stale:
        return "bound_file_missing"
    if inputs.binding_missing:
        return "file_exists_unbound"
    return "consistent"


def _final_file_state(inputs: SlotInputs, *, unbound_title: str) -> dict[str, Any]:
    if inputs.binding_missing:
        return _pending_binding(unbound_title)
    return _done("file_exists", binding_consistency=_file_binding_consistency(inputs), title="Final Video 已存在")


def derive_video_plan_slot_states(inputs: SlotInputs, execution: dict[str, str] | None = None) -> dict[str, dict[str, Any]]:
    execution = execution or {}
    states = {
        "audio": _done("file_exists", title="音频已存在") if inputs.audio_exists else _pending("audio_external_plan", "音频由独立 Plan 补齐"),
        "image_prompt": (
            _done("prompt_file_exists", title="Image Prompt 已存在")
            if inputs.image_prompt_exists
            else _skipped(title="新图或下游产物已存在，Image Prompt 无需执行")
            if inputs.image_exists or inputs.raw_exists or inputs.final_exists
            else _pending("source_ready", "可生成 Image Prompt")
            if inputs.source_exists
            else _blocked(title="缺少原图")
        ),
        "image": (
            _done("file_exists", title="新图已存在")
            if inputs.image_exists
            else _skipped(title="Raw 或 Final 已存在，新图无需执行")
            if inputs.raw_exists or inputs.final_exists
            else _pending("source_ready", "可从原图补新图")
            if inputs.source_exists
            else _blocked(title="缺少原图")
        ),
        "video_prompt": (
            _done("prompt_file_exists", title="Video Prompt 已存在")
            if inputs.video_prompt_exists
            else _skipped(title="Raw 或 Final 已存在，Video Prompt 无需执行")
            if inputs.raw_exists or inputs.final_exists
            else _pending("image_ready", "可生成 Video Prompt")
            if inputs.image_exists
            else _blocked(title="缺少新图")
        ),
        "raw_video": (
            _done("file_exists", title="Raw Video 已存在")
            if inputs.raw_exists
            else _skipped(title="Final 已存在，Raw 无需执行")
            if inputs.final_exists
            else _pending("image_ready", "可生成 Raw Video")
            if inputs.image_exists
            else _blocked(title="缺少新图")
        ),
        "final_video": (
            _final_file_state(inputs, unbound_title="Final Video 已存在，待绑定 StoryBoard")
            if inputs.final_exists
            else _pending("raw_and_audio_ready", "可执行音频合成")
            if inputs.raw_exists and inputs.audio_exists
            else _blocked("raw_ready_missing_audio", "缺少音频")
            if inputs.raw_exists
            else _blocked(title="缺少 Raw Video")
        ),
    }
    return {key: _apply_execution(value, execution.get(key, "")) for key, value in states.items()}


def derive_image_plan_slot_states(inputs: SlotInputs, execution: dict[str, str] | None = None) -> dict[str, dict[str, Any]]:
    execution = execution or {}
    states = {
        "image_prompt": (
            _done("prompt_file_exists", title="Image Prompt 已存在")
            if inputs.image_prompt_exists
            else _skipped(title="新图或下游产物已存在，Image Prompt 无需执行")
            if inputs.image_exists or inputs.raw_exists or inputs.final_exists
            else _pending("source_ready", "可生成 Image Prompt")
            if inputs.source_exists
            else _blocked(title="缺少原图")
        ),
        "image": (
            _done("file_exists", title="新图已存在")
            if inputs.image_exists
            else _skipped(title="Raw 或 Final 已存在，新图无需执行")
            if inputs.raw_exists or inputs.final_exists
            else _pending("source_and_prompt_ready", "可生成新图")
            if inputs.source_exists and inputs.image_prompt_exists
            else _blocked(title="缺少原图或 Image Prompt")
        ),
    }
    return {key: _apply_execution(value, execution.get(key, "")) for key, value in states.items()}


def derive_video_only_plan_slot_states(inputs: SlotInputs, execution: dict[str, str] | None = None) -> dict[str, dict[str, Any]]:
    execution = execution or {}
    states = {
        "audio": _done("file_exists", title="音频已存在") if inputs.audio_exists else _pending("audio_external_plan", "音频由独立 Plan 补齐"),
        "image": (
            _done("file_exists", title="新图已存在")
            if inputs.image_exists
            else _skipped(title="Raw 或 Final 已存在，新图无需执行")
            if inputs.raw_exists or inputs.final_exists
            else _pending("source_ready", "可补新图")
            if inputs.source_exists
            else _blocked(title="缺少原图")
        ),
        "video_prompt": (
            _done("prompt_file_exists", title="Video Prompt 已存在")
            if inputs.video_prompt_exists
            else _skipped(title="Raw 或 Final 已存在，Video Prompt 无需执行")
            if inputs.raw_exists or inputs.final_exists
            else _pending("image_ready", "可生成 Video Prompt")
            if inputs.image_exists
            else _blocked(title="缺少新图")
        ),
        "raw_video": (
            _done("file_exists", title="Raw Video 已存在")
            if inputs.raw_exists
            else _skipped(title="Final 已存在，Raw 无需执行")
            if inputs.final_exists
            else _pending("image_and_prompt_ready", "可生成 Raw Video")
            if inputs.image_exists and inputs.video_prompt_exists
            else _blocked(title="缺少新图或 Video Prompt")
        ),
        "copy_final": (
            _final_file_state(inputs, unbound_title="Final Video 已存在，待确认绑定")
            if inputs.final_exists
            else _pending("raw_ready", "可拷贝成终视频")
            if inputs.raw_exists
            else _blocked(title="缺少 Raw Video")
        ),
    }
    return {key: _apply_execution(value, execution.get(key, "")) for key, value in states.items()}


def derive_all_plan_slot_states(inputs: SlotInputs, execution: dict[str, dict[str, str]] | None = None) -> dict[str, dict[str, dict[str, Any]]]:
    execution = execution or {}
    return {
        "video_plan": derive_video_plan_slot_states(inputs, execution.get("video_plan")),
        "image_plan": derive_image_plan_slot_states(inputs, execution.get("image_plan")),
        "video_only_plan": derive_video_only_plan_slot_states(inputs, execution.get("video_only_plan")),
    }
