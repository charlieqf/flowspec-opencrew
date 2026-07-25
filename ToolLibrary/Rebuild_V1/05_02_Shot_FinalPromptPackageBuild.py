from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from typing import Any


TOOL_ID = "05_02_Shot_FinalPromptPackageBuild"
TOOL_NAME = "Shot Final Prompt Package Build"
TOOL_VERSION = "1.0.0"
REQUIRES = ["rebuild_shot_plan.json", "scene_srt", "scene_prompt", "tts_selection", "shot_id"]
PRODUCES = [
    "rebuild_shot_plan.json",
    "Assets/<variant_id>/<shot_id>/final_prompt_package.json",
    f"reports/rebuild_v1/{TOOL_ID}.json",
]
SUGGESTED_PREVIOUS_TOOLS = ["05_01_Shot_ScenePromptRefresh"]
SUGGESTED_NEXT_TOOLS = [
    "07_01_Shot_PlanA_TTSPromptBuild",
    "07_02_Shot_PlanA_TTSGenerateAndLock",
    "06_01_Shot_PlanA_SceneImageRebuild",
    "11_01_Shot_PlanC_ReadinessCheck",
]
DEFAULT_VIDEO_TARGET_MODEL = "grok"
DEFAULT_VARIANT_ID = "variant_001"


class ToolError(RuntimeError):
    pass


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def now_ms() -> int:
    return int(time.time() * 1000)


def safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "_" for ch in str(value or "")) or "item"


def rel(workspace: Path, path: Path | str | None) -> str:
    if not path:
        return ""
    candidate = Path(str(path))
    if not candidate.is_absolute():
        return candidate.as_posix()
    try:
        return candidate.resolve().relative_to(workspace.resolve()).as_posix()
    except ValueError:
        return candidate.as_posix()


def strip_srt_timing(text: str) -> str:
    lines = []
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.isdigit() or "-->" in stripped:
            continue
        lines.append(stripped)
    return " ".join(lines).strip()


def shot_list(plan: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in plan.get("shots", []) if isinstance(item, dict)]


def shot_id_of(shot: dict[str, Any]) -> str:
    return str(shot.get("shot_id") or shot.get("id") or "")


def scene_id_of(mark: dict[str, Any]) -> str:
    return str(mark.get("scene_mark_id") or mark.get("id") or "")


def scene_marks_for_shot(shot: dict[str, Any]) -> list[dict[str, Any]]:
    reference = shot.get("reference") if isinstance(shot.get("reference"), dict) else {}
    return [item for item in reference.get("scene_marks", []) if isinstance(item, dict)]


def target_shots(plan: dict[str, Any], args: argparse.Namespace) -> list[dict[str, Any]]:
    wanted = {str(item) for item in args.shot_id if str(item)}
    shots = [shot for shot in shot_list(plan) if not wanted or shot_id_of(shot) in wanted]
    if wanted and not shots:
        raise ToolError(f"No shots matched --shot-id: {sorted(wanted)}")
    return shots


def scene_text(mark: dict[str, Any], fallback: str = "") -> str:
    for key in ("srt_text", "scene_srt", "text", "subtitle"):
        value = str(mark.get(key) or "").strip()
        if value:
            return strip_srt_timing(value)
    desc = mark.get("scene_description") if isinstance(mark.get("scene_description"), dict) else {}
    for key in ("srt_text", "summary", "video_prompt", "prompt"):
        value = str(desc.get(key) or "").strip()
        if value:
            return strip_srt_timing(value)
    return strip_srt_timing(str(fallback or ""))


def spoken_script(shot: dict[str, Any]) -> str:
    for key in ("spoken_script", "script", "srt_text"):
        value = str(shot.get(key) or "").strip()
        if value:
            return strip_srt_timing(value)
    reference = shot.get("reference") if isinstance(shot.get("reference"), dict) else {}
    return strip_srt_timing(str(reference.get("srt_text") or ""))


def shot_tts_source(shot: dict[str, Any]) -> dict[str, Any]:
    scene_items = []
    for mark in scene_marks_for_shot(shot):
        text = scene_text(mark)
        if text:
            scene_items.append({"scene_mark_id": scene_id_of(mark), "text": text})
    text_value = " ".join(item["text"] for item in scene_items).strip() or spoken_script(shot)
    return {"text": strip_srt_timing(text_value), "source": "scene_srt" if scene_items else "spoken_script", "scene_items": scene_items}


def variant_shot_dir(workspace: Path, shot_id: str, variant_id: str) -> Path:
    return workspace / "Assets" / variant_id / safe_name(shot_id)


def reference_manifest() -> list[dict[str, Any]]:
    root = Path(__file__).with_name("prompt_references")
    files = [
        "GEMINI_TTS_PROMPT_OPTIMIZER_ALGORITHM.md",
        "工作方式指南_实验规划-标准-总结-提升.MD",
        "提示词撰写指南_口播_人物产品一致性模型_GPT.MD",
    ]
    manifest = []
    for name in files:
        path = root / name
        manifest.append({"name": name, "path": str(path), "available": path.exists(), "bytes": path.stat().st_size if path.exists() else 0})
    return manifest


def extract_speed_notes(selection: dict[str, Any]) -> list[str]:
    blob = " ".join(str(selection.get(key) or "") for key in ("prompt", "prompt_template", "instructions"))
    notes = []
    for pattern in (r"语速[^;；。,.，]*", r"停顿[^;；。,.，]*", r"节奏[^;；。,.，]*", r"speed[^;；。,.，]*", r"pace[^;；。,.，]*"):
        notes.extend(match.strip() for match in re.findall(pattern, blob, flags=re.I) if match.strip())
    return list(dict.fromkeys(notes))


def tts_execution_prompt(selection: dict[str, Any], text_value: str) -> str:
    provider = str(selection.get("provider") or "").strip().lower()
    template = str(selection.get("prompt_template") or "").strip()
    prompt = str(selection.get("prompt") or selection.get("instructions") or "").strip()
    if template:
        return template.replace("{text}", text_value) if "{text}" in template else f"{template}\n正文：{text_value}"
    if provider in {"google", "gemini"}:
        return prompt.replace("{text}", text_value) if "{text}" in prompt else f"{prompt or '请用自然真实的中文短视频口播语气朗读，且只输出正文语音。'}\n正文：{text_value}"
    return prompt or "年轻中国女性生活短视频口播；近距离自然收音；只朗读正文，不添加额外内容；语速更快一点，停顿更短；中高音区，明亮但不尖；清透温暖。"


def scene_visual_prompt(mark: dict[str, Any]) -> str:
    desc = mark.get("scene_description") if isinstance(mark.get("scene_description"), dict) else {}
    for key in ("video_prompt", "motion_prompt", "visual_prompt", "prompt", "summary"):
        value = str(desc.get(key) or "").strip()
        if value:
            return value
    return scene_text(mark)


def build_image_prompt(shot: dict[str, Any], mark: dict[str, Any]) -> str:
    visual = scene_visual_prompt(mark)
    text = scene_text(mark, shot_tts_source(shot).get("text") or spoken_script(shot))
    return (
        "Create the final realistic vertical 9:16 first-frame image for this rebuilt short-video scene. "
        "Use the confirmed first/last reference frames as the source of composition, camera distance, lighting, pose, scene geometry, host/product identity, and real phone-video texture. "
        "Apply the replacement intent without inventing a new layout. Remove subtitles, watermarks, UI overlays, account names, logos, and unreadable text unless the product packaging identity explicitly requires it. "
        "Keep hands, face, product packaging, shadows, table/furniture, and perspective coherent. Avoid medical claims, before/after claims, authority claims, and exaggerated efficacy cues. "
        f"Scene narration: {text}. Visual execution: {visual}"
    )


def build_grok_video_prompt(shot: dict[str, Any], mark: dict[str, Any]) -> str:
    desc = mark.get("scene_description") if isinstance(mark.get("scene_description"), dict) else {}
    notes = desc.get("model_notes") if isinstance(desc.get("model_notes"), dict) else {}
    grok_note = str(notes.get("grok") or "").strip()
    visual = scene_visual_prompt(mark)
    text = scene_text(mark, shot_tts_source(shot).get("text") or spoken_script(shot))
    return (
        "Create a realistic vertical 9:16 Grok/xAI image-to-video clip from the provided reference image. "
        "Preserve the reference image as the dominant first-frame identity: same host/product, same composition, same camera angle, same lighting, same hand/product placement, and the same casual phone-video realism. "
        "Use subtle natural motion only: small body movement, product handling, mouth/gesture continuity, gentle handheld micro-movement, and realistic shadows. "
        "Do not add subtitles, captions, watermarks, UI text, logos, extra brand text, medical claims, authority claims, before-after comparisons, or exaggerated cure language. "
        "Keep product packaging readable only when it is already part of the approved product identity; otherwise avoid new readable text. "
        f"Narration context: {text}. Visual action: {visual}. Grok-specific note: {grok_note or 'Keep the result natural, social-video realistic, and not studio-polished.'}"
    )


def build_structured_video_prompt(shot: dict[str, Any], mark: dict[str, Any], tts_payload: dict[str, Any]) -> dict[str, Any]:
    desc = mark.get("scene_description") if isinstance(mark.get("scene_description"), dict) else {}
    notes = desc.get("model_notes") if isinstance(desc.get("model_notes"), dict) else {}
    grok_note = str(notes.get("grok") or "").strip()
    visual = scene_visual_prompt(mark)
    text = scene_text(mark, tts_payload.get("text") or spoken_script(shot))
    speed_notes = "，".join(str(item) for item in tts_payload.get("speed_notes") or [] if str(item).strip())
    negative = negative_prompt(mark)
    return {
        "zh": {
            "positive": "生成真实竖屏 9:16 的 Grok/xAI 图生视频片段，保持生活短视频/手机自拍视频的自然真实质感。",
            "character_action": f"人物自然口播，保持参考图中的姿态、表情、手部位置和产品拿法，只加入轻微身体动作、产品拿取动作和口型连续性。画面动作：{visual}",
            "speech_speed": speed_notes or "自然生活化口播节奏，语速略快，停顿较短。",
            "voice_description": f"口播内容：{text}。年轻女性生活短视频口播感，近距离自然收音，不要播音腔。",
            "camera_motion": "轻微手持微动，保持稳定构图，不做夸张推拉或电影化运镜。",
            "scene_consistency": "保持参考图的构图、机位、光线、背景、厨房/桌面环境、阴影和真实手机画质。",
            "product_consistency": "保持产品包装、颜色、手持位置、透视关系和已确认的可读包装信息；不要新增未经确认的品牌文字。",
            "negative": negative,
            "model_notes": grok_note or "Grok/xAI：保持自然、真实、社媒短视频质感，不要棚拍广告片感。",
        },
        "en": {
            "positive": "Create a realistic vertical 9:16 Grok/xAI image-to-video clip with casual short-video phone realism.",
            "character_action": f"Keep the host naturally speaking. Preserve the reference pose, expression, hand placement, and product handling. Add only subtle body movement, product handling, and mouth continuity. Visual action: {visual}",
            "speech_speed": speed_notes or "Natural short-video speaking rhythm, slightly faster pace, shorter pauses.",
            "voice_description": f"Narration text: {text}. Young female short-video spoken delivery, close natural recording feel, not announcer-like.",
            "camera_motion": "Use gentle handheld micro-movement with stable framing. Avoid dramatic push-ins or cinematic camera moves.",
            "scene_consistency": "Preserve the reference composition, camera angle, lighting, background, kitchen/table setting, shadows, and real phone-video texture.",
            "product_consistency": "Keep product packaging, colors, hand placement, perspective, and approved readable packaging details consistent; do not invent unapproved brand text.",
            "negative": negative,
            "model_notes": grok_note or "Grok/xAI: keep the result natural, social-video realistic, and not studio-polished.",
        },
    }


def compile_structured_video_prompt(structured: dict[str, Any], language: str = "en") -> str:
    labels = {
        "zh": {
            "positive": "正向",
            "character_action": "人物动作",
            "speech_speed": "语言速度",
            "voice_description": "语音描述",
            "camera_motion": "镜头运动",
            "scene_consistency": "场景一致性",
            "product_consistency": "产品一致性",
            "negative": "负向",
            "model_notes": "模型备注",
        },
        "en": {
            "positive": "Positive",
            "character_action": "Character Action",
            "speech_speed": "Speech Speed",
            "voice_description": "Voice Description",
            "camera_motion": "Camera Motion",
            "scene_consistency": "Scene Consistency",
            "product_consistency": "Product Consistency",
            "negative": "Negative",
            "model_notes": "Model Notes",
        },
    }
    lang = "zh" if language == "zh" else "en"
    values = structured.get(lang) if isinstance(structured.get(lang), dict) else {}
    return "\n\n".join(f"{labels[lang][key]}:\n{values[key]}" for key in labels[lang] if str(values.get(key) or "").strip())


def negative_prompt(mark: dict[str, Any]) -> str:
    desc = mark.get("scene_description") if isinstance(mark.get("scene_description"), dict) else {}
    value = str(desc.get("negative_prompt") or "").strip()
    base = "watermark, logo, subtitles, captions, UI text, unreadable text, distorted face, bad hands, duplicated fingers, product deformation, medical claims, cure claims, before-after claims, low quality"
    return value if value else base


def build_prompt_package(workspace: Path, shot: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    shot_id = shot_id_of(shot)
    selection = shot.get("tts_selection") if isinstance(shot.get("tts_selection"), dict) else {}
    tts_source = shot_tts_source(shot)
    text_value = str(tts_source.get("text") or "").strip()
    if not text_value:
        raise ToolError(f"{shot_id}: missing TTS text")

    tts_prompt = tts_execution_prompt(selection, text_value)
    tts_payload = {
        "provider": selection.get("provider") or args.tts_provider,
        "model": selection.get("model") or args.tts_model,
        "voice": selection.get("voice") or args.tts_voice,
        "text": text_value,
        "text_source": tts_source.get("source"),
        "execution_prompt": tts_prompt,
        "speed_notes": extract_speed_notes(selection),
        "selection_source": selection.get("selection_source") or "",
    }

    scenes = []
    for mark in scene_marks_for_shot(shot):
        scene_id = scene_id_of(mark)
        structured_video_prompt = build_structured_video_prompt(shot, mark, tts_payload)
        active_language = "en"
        compiled_video_prompt = compile_structured_video_prompt(structured_video_prompt, active_language)
        prompts = {
            "scene_mark_id": scene_id,
            "target_video_model": args.video_target_model,
            "srt_text": scene_text(mark, text_value),
            "image_prompt": build_image_prompt(shot, mark),
            "active_language": active_language,
            "video_prompt_structured": structured_video_prompt,
            "video_prompt": compiled_video_prompt or build_grok_video_prompt(shot, mark),
            "grok_video_prompt": compiled_video_prompt or build_grok_video_prompt(shot, mark),
            "negative_prompt": negative_prompt(mark),
            "reference_guides_used": [item["name"] for item in reference_manifest() if item["available"]],
        }
        mark["final_prompts"] = {
            "tool": TOOL_ID,
            "generated_at": now_ms(),
            "target_video_model": args.video_target_model,
            "image_prompt": prompts["image_prompt"],
            "active_language": active_language,
            "video_prompt_structured": structured_video_prompt,
            "video_prompt": prompts["video_prompt"],
            "grok_video_prompt": prompts["grok_video_prompt"],
            "negative_prompt": prompts["negative_prompt"],
        }
        scenes.append(prompts)

    shot.setdefault("plan_a", {})["tts_prompt"] = tts_prompt
    shot["final_prompt_package"] = {
        "path": rel(workspace, variant_shot_dir(workspace, shot_id, args.variant_id) / "final_prompt_package.json"),
        "tool": TOOL_ID,
        "generated_at": now_ms(),
        "target_video_model": args.video_target_model,
        "tts": tts_payload,
        "scene_count": len(scenes),
    }
    return {
        "tool_id": TOOL_ID,
        "tool_version": TOOL_VERSION,
        "status": "completed",
        "shot_id": shot_id,
        "variant_id": args.variant_id,
        "generated_at": now_ms(),
        "reference_guides": reference_manifest(),
        "defaults": {"video_target_model": args.video_target_model},
        "tts": tts_payload,
        "scenes": scenes,
    }


def scope(args: argparse.Namespace) -> dict[str, Any]:
    return {"shot_id": args.shot_id, "variant_id": args.variant_id}


def check_dependencies(workspace: Path, args: argparse.Namespace) -> dict[str, Any]:
    satisfied: list[Any] = []
    missing: list[dict[str, Any]] = []
    plan_path = workspace / args.input
    if len(args.shot_id) != 1:
        missing.append({"dependency": "shot_id", "reason": "shot-level tool requires exactly one --shot-id", "suggested_tools": SUGGESTED_PREVIOUS_TOOLS, "scope": scope(args)})
    if plan_path.exists():
        satisfied.append("rebuild_shot_plan.json")
        try:
            plan = read_json(plan_path)
            shots = target_shots(plan, args) if len(args.shot_id) == 1 else []
            for shot in shots:
                if shot_tts_source(shot).get("text"):
                    satisfied.append("scene_srt")
                else:
                    missing.append({"dependency": "scene_srt", "reason": f"{shot_id_of(shot)} has no scene SRT / spoken script", "suggested_tools": SUGGESTED_PREVIOUS_TOOLS, "scope": scope(args)})
                if scene_marks_for_shot(shot):
                    satisfied.append("scene_prompt")
                else:
                    missing.append({"dependency": "scene_prompt", "reason": f"{shot_id_of(shot)} has no scene marks", "suggested_tools": SUGGESTED_PREVIOUS_TOOLS, "scope": scope(args)})
                if isinstance(shot.get("tts_selection"), dict) and shot.get("tts_selection"):
                    satisfied.append("tts_selection")
                else:
                    missing.append({"dependency": "tts_selection", "reason": f"{shot_id_of(shot)} has no TTS selection", "suggested_tools": ["03_03_ShotPlan_TTSVoiceSelectionWrite"], "scope": scope(args)})
        except Exception as exc:
            missing.append({"dependency": "rebuild_shot_plan.json", "reason": f"failed to inspect shot plan: {exc}", "suggested_tools": ["02_Rebuild_ShotPlanBuilder"], "scope": scope(args)})
    else:
        missing.append({"dependency": "rebuild_shot_plan.json", "reason": f"required workspace file does not exist: {args.input}", "suggested_tools": ["02_Rebuild_ShotPlanBuilder"], "scope": scope(args)})
    missing_refs = [item for item in reference_manifest() if not item["available"]]
    warnings = [{"dependency": "prompt_references", "reason": f"missing reference guide: {item['name']}", "scope": scope(args)} for item in missing_refs]
    return {"status": "blocked" if missing else "warning" if warnings else "satisfied", "satisfied": satisfied, "missing": missing, "warnings": warnings}


def run(workspace: Path, args: argparse.Namespace) -> dict[str, Any]:
    plan = read_json(workspace / args.input)
    results = []
    blockers = []
    for shot in target_shots(plan, args):
        shot_id = shot_id_of(shot)
        try:
            package = build_prompt_package(workspace, shot, args)
            output_path = variant_shot_dir(workspace, shot_id, args.variant_id) / "final_prompt_package.json"
            write_json(output_path, package)
            results.append({"shot_id": shot_id, "status": "completed", "output": rel(workspace, output_path), "scene_count": len(package.get("scenes") or [])})
        except Exception as exc:
            blockers.append(f"{shot_id}: {exc}")
            results.append({"shot_id": shot_id, "status": "blocked", "blocking_reason": str(exc)})
    write_json(workspace / args.output, plan)
    report = {
        "status": "completed_with_blockers" if blockers else "completed",
        "tool_id": TOOL_ID,
        "tool_version": TOOL_VERSION,
        "blocking_errors": blockers,
        "result_count": len(results),
        "results": results,
    }
    write_json(workspace / "reports" / "rebuild_v1" / f"{TOOL_ID}.json", report)
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=f"Standalone Rebuild_V1 tool: {TOOL_ID}")
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--task-id", type=int, default=0)
    parser.add_argument("--session-id", type=int, default=0)
    parser.add_argument("--input", default="rebuild_shot_plan.json")
    parser.add_argument("--output", default="rebuild_shot_plan.json")
    parser.add_argument("--shot-id", action="append", default=[])
    parser.add_argument("--variant-id", default=DEFAULT_VARIANT_ID)
    parser.add_argument("--video-target-model", default=DEFAULT_VIDEO_TARGET_MODEL)
    parser.add_argument("--tts-provider", default="qwen")
    parser.add_argument("--tts-model", default="qwen3-tts-instruct-flash")
    parser.add_argument("--tts-voice", default="Cherry")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--check-dependencies-only", action="store_true")
    parser.add_argument("--print-json", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    workspace = args.workspace.expanduser().resolve()
    dependencies = check_dependencies(workspace, args)
    try:
        if args.check_dependencies_only or (dependencies["missing"] and not args.force):
            status, result = ("blocked" if dependencies["missing"] else "completed"), None
        else:
            result = run(workspace, args)
            status = result.get("status", "completed")
        payload = {
            "tool": TOOL_ID,
            "tool_version": TOOL_VERSION,
            "status": status,
            "workspace": str(workspace),
            "dependencies": dependencies,
            "suggested_previous_tools": SUGGESTED_PREVIOUS_TOOLS,
            "suggested_next_tools": SUGGESTED_NEXT_TOOLS,
            "result": result,
        }
    except Exception as exc:
        payload = {
            "tool": TOOL_ID,
            "tool_version": TOOL_VERSION,
            "status": "failed",
            "workspace": str(workspace),
            "message": str(exc),
            "dependencies": dependencies,
            "suggested_previous_tools": SUGGESTED_PREVIOUS_TOOLS,
            "suggested_next_tools": SUGGESTED_NEXT_TOOLS,
        }
    if args.print_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    if payload["status"] == "blocked":
        raise SystemExit(2)
    if payload["status"] == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
