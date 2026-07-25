from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any


TOOL_ID = "03_02_ShotPlan_TTSVoiceRecommend"
TOOL_NAME = TOOL_ID
TOOL_VERSION = "1.0.0"
DEFAULT_TTS_PROVIDER = "qwen"
DEFAULT_TTS_MODEL = "qwen3-tts-instruct-flash"
DEFAULT_TTS_VOICE = "Cherry"
REQUIRES = ["rebuild_shot_plan.json"]
OPTIONAL_INPUTS = ["tts/tts_reference_audio_manifest.json"]
PRODUCES = ["tts/tts_voice_recommendations.json", f"reports/rebuild_v1/{TOOL_ID}.json"]
SUGGESTED_PREVIOUS_TOOLS = ["02_Rebuild_ShotPlanBuilder", "03_01_ShotPlan_TTSReferenceAudioExtract"]
SUGGESTED_NEXT_TOOLS = ["03_03_ShotPlan_TTSVoiceSelectionWrite"]


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def now_ms() -> int:
    return int(time.time() * 1000)


def shot_list(plan: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in plan.get("shots", []) if isinstance(item, dict)]


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def backend_import_path() -> Path:
    return repo_root() / "OpenCrew" / "backend"


def plain_srt_text(value: str) -> str:
    rows: list[str] = []
    for line in value.splitlines():
        text = line.strip()
        if not text or text.isdigit() or "-->" in text:
            continue
        rows.append(text)
    return re.sub(r"\s+", " ", " ".join(rows)).strip()


def shot_reference_text(shot: dict[str, Any]) -> str:
    reference = shot.get("reference") if isinstance(shot.get("reference"), dict) else {}
    for value in (
        reference.get("srt_text"),
        reference.get("spoken_text"),
        shot.get("spoken_text"),
        shot.get("voiceover"),
    ):
        if isinstance(value, str) and value.strip():
            return plain_srt_text(value)
    return ""


def plan_reference_text(plan: dict[str, Any]) -> str:
    return " ".join(text for text in (shot_reference_text(shot) for shot in shot_list(plan)) if text).strip()


def load_reference_audio_manifest(workspace: Path) -> dict[str, Any]:
    path = workspace / "tts" / "tts_reference_audio_manifest.json"
    if not path.exists():
        return {}
    payload = read_json(path)
    return payload if isinstance(payload, dict) else {}


def reference_audio_path(manifest: dict[str, Any]) -> Path | None:
    reference_audio = manifest.get("reference_audio") if isinstance(manifest.get("reference_audio"), dict) else {}
    value = reference_audio.get("path")
    if isinstance(value, str) and value.strip():
        path = Path(value).expanduser()
        if path.exists() and path.is_file():
            return path
    return None


def load_candidate_profiles(provider: str) -> list[dict[str, Any]]:
    root = repo_root() / "OpenCrew" / "ModelConfig" / "tts_voice_previews"
    candidates: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*.json")):
        try:
            payload = read_json(path)
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        if provider and str(payload.get("provider") or "") != provider:
            continue
        if not (payload.get("voice_id") and isinstance(payload.get("features"), dict) and isinstance(payload.get("profile"), dict)):
            continue
        preview_path = Path(str(payload.get("preview_audio_path") or "")).expanduser()
        if not preview_path.exists():
            continue
        candidates.append({**payload, "_profile_json_path": str(path), "_preview_audio_path": str(preview_path)})
    return candidates


def match_reference_voice(plan: dict[str, Any], manifest: dict[str, Any], args: argparse.Namespace) -> dict[str, Any] | None:
    path = reference_audio_path(manifest)
    if not path:
        return None
    sys.path.insert(0, str(backend_import_path()))
    from opcrew_backend.routes.media_model_config import (  # type: ignore
        TTSVoiceMatchPayload,
        candidate_profile_from_voice,
        load_audio_features,
        profile_match_score,
        public_feature_summary,
        reference_profile_from_payload,
        voice_prefilter_reason,
    )

    reference_text = plan_reference_text(plan)
    payload = TTSVoiceMatchPayload(
        reference_audio_path=str(path),
        reference_text=reference_text,
        target_gender=str(args.target_gender or ""),
        sample_text=str(args.sample_text or ""),
        language=str(args.language or "zh"),
        top_k=max(1, int(args.top_k or 5)),
        regenerate=False,
    )
    reference_features = load_audio_features(path)
    reference_profile = reference_profile_from_payload(payload, reference_features)
    ranked: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for candidate in load_candidate_profiles(str(args.tts_provider or DEFAULT_TTS_PROVIDER)):
        reason = voice_prefilter_reason(reference_profile, candidate)
        if reason:
            skipped.append({"voice_id": str(candidate.get("voice_id") or ""), "reason": reason})
            continue
        preview_path = Path(str(candidate.get("_preview_audio_path") or "")).expanduser()
        candidate_features = load_audio_features(preview_path)
        sample_text = str(args.sample_text or "")
        candidate_profile = candidate_profile_from_voice(candidate, sample_text, candidate_features)
        score, parts, gender_mismatch = profile_match_score(reference_features, candidate_features, reference_profile, candidate_profile)
        if gender_mismatch:
            skipped.append({"voice_id": str(candidate.get("voice_id") or ""), "reason": "audio_gender_mismatch"})
            continue
        ranked.append({
            "provider": candidate.get("provider"),
            "candidate_model": candidate.get("model"),
            "voice": candidate.get("voice_id"),
            "voice_id": candidate.get("voice_id"),
            "label": candidate.get("label") or candidate.get("voice_id"),
            "score": round(float(score), 4),
            "score_parts": {key: round(float(value), 4) for key, value in parts.items()},
            "profile_json_path": candidate.get("_profile_json_path"),
            "preview_audio_path": candidate.get("_preview_audio_path"),
            "features": public_feature_summary(candidate_features),
            "candidate_profile": candidate_profile,
        })
    ranked = sorted(ranked, key=lambda item: float(item.get("score") or 0), reverse=True)
    if not ranked:
        return None
    return {
        "reference_audio": str(path),
        "reference_text_chars": len(reference_text),
        "reference_profile": reference_profile,
        "reference_features": public_feature_summary(reference_features),
        "top": ranked[:max(1, int(args.top_k or 5))],
        "skipped_count": len(skipped),
    }


def recommend_voice_for_shot(shot: dict[str, Any], args: argparse.Namespace, match: dict[str, Any] | None) -> dict[str, Any]:
    shot_id = str(shot.get("shot_id") or "")
    role = str(shot.get("role") or shot.get("formula_slot") or "").strip()
    top = match.get("top", [])[0] if isinstance(match, dict) and isinstance(match.get("top"), list) and match.get("top") else {}
    if top:
        return {
            "shot_id": shot_id,
            "provider": top.get("provider") or args.tts_provider or DEFAULT_TTS_PROVIDER,
            "model": args.tts_model or DEFAULT_TTS_MODEL,
            "voice": top.get("voice") or args.tts_voice or DEFAULT_TTS_VOICE,
            "label": top.get("label") or top.get("voice") or "",
            "score": top.get("score"),
            "reason": f"matched Analysis reference audio for {role or 'shot'}",
            "match_source": "analysis_reference_audio",
        }
    return {
        "shot_id": shot_id,
        "provider": args.tts_provider or DEFAULT_TTS_PROVIDER,
        "model": args.tts_model or DEFAULT_TTS_MODEL,
        "voice": args.tts_voice or DEFAULT_TTS_VOICE,
        "reason": f"default voice selection for {role or 'shot'}",
    }


def run(workspace: Path, args: argparse.Namespace) -> dict[str, Any]:
    plan = read_json(workspace / args.input)
    manifest = load_reference_audio_manifest(workspace)
    match: dict[str, Any] | None = None
    warnings: list[str] = []
    try:
        match = match_reference_voice(plan, manifest, args)
    except Exception as exc:
        warnings.append(f"reference voice matching failed; using default voice selection: {exc}")
    recommendations = [recommend_voice_for_shot(shot, args, match) for shot in shot_list(plan)]
    manifest_path = workspace / "tts" / "tts_reference_audio_manifest.json"
    payload = {
        "tool": TOOL_ID,
        "tool_version": TOOL_VERSION,
        "generated_at": now_ms(),
        "status": "completed",
        "recommendations": recommendations,
        "match_result": match or {},
        "reference_audio_manifest": "tts/tts_reference_audio_manifest.json" if manifest_path.exists() else "",
        "warnings": warnings if warnings else [] if manifest_path.exists() else ["optional tts reference audio manifest is absent"],
    }
    write_json(workspace / "tts" / "tts_voice_recommendations.json", payload)
    write_json(workspace / "reports" / "rebuild_v1" / f"{TOOL_ID}.json", payload)
    return payload


def check_dependencies(workspace: Path, args: argparse.Namespace) -> dict[str, Any]:
    satisfied: list[str] = []
    missing: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    input_path = workspace / args.input
    if input_path.exists():
        satisfied.append("rebuild_shot_plan.json")
    else:
        missing.append({"dependency": "rebuild_shot_plan.json", "reason": f"required workspace file does not exist: {args.input}", "suggested_tools": ["02_Rebuild_ShotPlanBuilder"]})
    for optional in OPTIONAL_INPUTS:
        if not (workspace / optional).exists():
            warnings.append({"dependency": optional, "reason": "optional TTS reference audio manifest is absent; tool will use default voice selection"})
    return {"status": "blocked" if missing else "warning" if warnings else "satisfied", "satisfied": satisfied, "missing": missing, "warnings": warnings}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=f"Standalone Rebuild_V1 tool: {TOOL_ID}")
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--task-id", type=int, default=0)
    parser.add_argument("--session-id", type=int, default=0)
    parser.add_argument("--input", default="rebuild_shot_plan.json")
    parser.add_argument("--output", default="tts/tts_voice_recommendations.json")
    parser.add_argument("--source-package", default="source_package.json")
    parser.add_argument("--database-url", default="")
    parser.add_argument("--database-url-env", default="OPENCREW_DATABASE_URL")
    parser.add_argument("--tts-provider", default=DEFAULT_TTS_PROVIDER)
    parser.add_argument("--tts-model", default=DEFAULT_TTS_MODEL)
    parser.add_argument("--tts-voice", default=DEFAULT_TTS_VOICE)
    parser.add_argument("--target-gender", default="")
    parser.add_argument("--language", default="zh")
    parser.add_argument("--sample-text", default="欢迎使用 OpenCrew。下面这段声音将用于测试语气、节奏、清晰度和商业短视频旁白的自然程度。")
    parser.add_argument("--top-k", type=int, default=5)
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
        payload = {"tool": TOOL_ID, "tool_version": TOOL_VERSION, "status": status, "workspace": str(workspace), "dependencies": dependencies, "suggested_previous_tools": SUGGESTED_PREVIOUS_TOOLS, "suggested_next_tools": SUGGESTED_NEXT_TOOLS, "result": result}
    except Exception as exc:
        payload = {"tool": TOOL_ID, "tool_version": TOOL_VERSION, "status": "failed", "workspace": str(workspace), "message": str(exc), "dependencies": dependencies, "suggested_previous_tools": SUGGESTED_PREVIOUS_TOOLS, "suggested_next_tools": SUGGESTED_NEXT_TOOLS}
    if args.print_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    if payload["status"] == "blocked":
        raise SystemExit(2)
    if payload["status"] == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
