"""Deterministic mock tools for the FlowSpec OpenCrew AI-video demo."""

from __future__ import annotations

from typing import Any


def done(artifacts: dict[str, Any], patch: dict[str, Any] | None = None, usage: list[dict[str, Any]] | None = None):
    return {"status": "completed", "artifacts": artifacts, "context_patch": patch or {}, "usage": usage or []}


def build_creative_brief(ctx):
    brief = {"project_id": ctx.variables["project_id"], "creative_revision": ctx.variables["creative_revision"], "brand": ctx.variables["brand"], "product": ctx.variables["product"], "audience": ctx.variables["audience"], "channel": ctx.variables["channel"], "constraints": ["no medical claims", "product identity must remain bound", "human publish approval required"], "mock_model_output": True}
    brief_hash = f"mock:{ctx.variables['project_id']}:{ctx.variables['creative_revision']}"
    agent_execution_id = f"agent_{ctx.run_id}_creative"
    return done(
        {"CreativeBrief.json": brief},
        {"brief_hash": brief_hash},
        [
            {
                "agent_execution_id": agent_execution_id,
                "reserve_amount": "0.012",
                "cost_amount": "0.008",
                "usage": {
                    "measurement_status": "locally_measured",
                    "input_tokens": 900,
                    "output_tokens": 180,
                    "reasoning_tokens": 100,
                    "total_tokens": 1080,
                    "provider_units": {"agent_turn": 1, "tool_calls": 1},
                },
            },
            {
                "agent_execution_id": agent_execution_id,
                "reserve_amount": "0.016",
                "cost_amount": "0.012",
                "usage": {
                    "measurement_status": "locally_measured",
                    "input_tokens": 900,
                    "output_tokens": 240,
                    "reasoning_tokens": 160,
                    "total_tokens": 1140,
                    "provider_units": {"agent_turn": 2, "tool_calls": 1},
                },
            },
        ],
    )


def plan_storyboard(ctx):
    dialogues = ctx.variables["storyboard_fixture"]
    return done({"StoryboardPlan.json": {"project_id": ctx.variables["project_id"], "creative_revision": ctx.variables["creative_revision"], "dialogues": dialogues, "mock_model_output": True}}, {"dialogue_count": len(dialogues)}, [{"reserve_amount": "0.026", "cost_amount": "0.018", "usage": {"measurement_status": "locally_measured", "input_tokens": 2100, "output_tokens": 760, "total_tokens": 2860}}])


def review_storyboard(ctx):
    fixture = ctx.human_decision()
    return done({"StoryboardApproval.json": {"decision": fixture["decision"], "actor": fixture["actor"], "reason": fixture["reason"], "expected_revision": fixture["expected_revision"]}}, {"storyboard_decision": fixture["decision"]})


def request_storyboard_revision(ctx):
    return done({"StoryboardRevisionRequest.json": {"project_id": ctx.variables["project_id"], "creative_revision": ctx.variables["creative_revision"], "scope": ["storyboard"], "reason": ctx.artifact("StoryboardApproval.json")["reason"], "status": "open"}})


def generate_consistency_references(ctx):
    return done({"ConsistencyReferences.json": {"project_id": ctx.variables["project_id"], "reference_keys": ["host-reference-v1", "product-reference-v3"], "mock_model_output": True}}, usage=[{"reserve_amount": "0.080", "cost_amount": "0.060", "usage": {"measurement_status": "locally_measured", "provider_units": {"image_count": 1}}}])


def generate_dialogue_audio(ctx):
    item = ctx.item
    duration = float(item["duration_seconds"])
    return done({"Audio_{asset_key}.wav": {"asset_key": item["asset_key"], "kind": "audio", "duration_seconds": duration, "provider_operation_key": f"tts:{ctx.run_id}:{item['asset_key']}", "voice": "mock-voice-au-01", "mock": True}}, usage=[{"reserve_amount": "0.016", "cost_amount": "0.012", "usage": {"measurement_status": "locally_measured", "input_tokens": len(item["text"].split()) * 3, "provider_units": {"audio_seconds": duration, "character_count": len(item["text"])}}}])


def create_visual_prompt(ctx):
    item = ctx.item
    return done({"VisualPrompt_{asset_key}.json": {"asset_key": item["asset_key"], "prompt": f"{item['visual']}; vertical phone-video composition; preserve approved host and product references", "reference_keys": ["host-reference-v1", "product-reference-v3"], "mock_model_output": True}}, usage=[{"reserve_amount": "0.009", "cost_amount": "0.006", "usage": {"measurement_status": "locally_measured", "input_tokens": 620, "output_tokens": 130, "total_tokens": 750}}])


def generate_dialogue_image(ctx):
    item = ctx.item
    prompt = ctx.artifact(f"VisualPrompt_{item['asset_key']}.json")
    return done({"Image_{asset_key}.png": {"asset_key": item["asset_key"], "kind": "image", "duration_seconds": 0, "provider_operation_key": f"image:{ctx.run_id}:{item['asset_key']}", "prompt_ref": prompt["asset_key"], "mock": True}}, usage=[{"reserve_amount": "0.055", "cost_amount": "0.040", "usage": {"measurement_status": "locally_measured", "provider_units": {"image_count": 1, "reference_count": 2}}}])


def generate_dialogue_video(ctx):
    item = ctx.item
    duration = float(item["duration_seconds"])
    return done({"RawVideo_{asset_key}.mp4": {"asset_key": item["asset_key"], "kind": "raw_video", "duration_seconds": duration, "provider_operation_key": f"video:{ctx.run_id}:{item['asset_key']}", "image_ref": f"Image_{item['asset_key']}.png", "mock": True}}, usage=[{"reserve_amount": "0.600", "cost_amount": "0.450", "usage": {"measurement_status": "locally_measured", "provider_units": {"video_seconds": duration, "input_image_count": 1}}}])


def bind_final_segment(ctx):
    item = ctx.item
    audio = ctx.artifact(f"Audio_{item['asset_key']}.wav")
    video = ctx.artifact(f"RawVideo_{item['asset_key']}.mp4")
    return done({"FinalSegment_{asset_key}.mp4": {"asset_key": item["asset_key"], "kind": "final_segment", "duration_seconds": audio["duration_seconds"], "provider_operation_key": f"local-bind:{ctx.run_id}:{item['asset_key']}", "bound_refs": [f"Audio_{item['asset_key']}.wav", f"RawVideo_{item['asset_key']}.mp4"], "source_video_duration": video["duration_seconds"], "mock": True}})


def compose_master_video(ctx):
    segments = ctx.artifact("FinalSegment_*.mp4")
    if isinstance(segments, dict):
        segments = [segments]
    keys = sorted(item["asset_key"] for item in segments)
    duration = sum(float(item["duration_seconds"]) for item in segments)
    master = {"asset_key": "master", "kind": "master_video", "duration_seconds": duration, "provider_operation_key": f"local-compose:{ctx.run_id}", "segment_keys": keys, "mock": True}
    manifest = {"project_id": ctx.variables["project_id"], "creative_revision": ctx.variables["creative_revision"], "segments": keys, "binding_status": "valid" if len(keys) == ctx.variables["dialogue_count"] else "unbound"}
    return done({"MasterVideo.mp4": master, "DeliveryManifest.json": manifest})


def run_multimodal_qa(ctx):
    passed = bool(ctx.variables["qa_pass_fixture"])
    revision_keys = [] if passed else ["dlg-002"]
    checks = [{"name": "media_decode", "passed": True}, {"name": "asset_binding", "passed": True}, {"name": "product_identity", "passed": passed}, {"name": "brand_claims", "passed": True}]
    return done({"QAReport.json": {"passed": passed, "checks": checks, "revision_asset_keys": revision_keys, "mock_model_output": True}}, {"qa_passed": passed}, [{"reserve_amount": "0.022", "cost_amount": "0.015", "usage": {"measurement_status": "locally_measured", "input_tokens": 1200, "output_tokens": 240, "total_tokens": 1440, "provider_units": {"video_seconds": ctx.artifact("MasterVideo.mp4")["duration_seconds"]}}}])


def request_qa_revision(ctx):
    qa = ctx.artifact("QAReport.json")
    return done({"QARevisionRequest.json": {"project_id": ctx.variables["project_id"], "creative_revision": ctx.variables["creative_revision"], "scope": qa["revision_asset_keys"], "reason": "Multimodal QA failed product-identity binding.", "status": "open"}})


def review_publish(ctx):
    fixture = ctx.human_decision()
    return done({"PublishApproval.json": {"decision": fixture["decision"], "actor": fixture["actor"], "reason": fixture["reason"], "expected_revision": fixture["expected_revision"]}}, {"publish_decision": fixture["decision"]})


def deliver_channel_asset(ctx):
    delivered_at = "2026-07-24T12:40:00Z"
    return done({"DeliveryReceipt.json": {"project_id": ctx.variables["project_id"], "channel": ctx.variables["channel"], "operation_key": f"deliver:{ctx.variables['project_id']}:{ctx.variables['creative_revision']}:{ctx.variables['channel']}", "status": "mock-delivered", "delivered_at": delivered_at}}, {"delivered_at": delivered_at})


def request_publish_revision(ctx):
    return done({"PublishRevisionRequest.json": {"project_id": ctx.variables["project_id"], "creative_revision": ctx.variables["creative_revision"], "scope": ["brand_review"], "reason": ctx.artifact("PublishApproval.json")["reason"], "status": "open"}})
