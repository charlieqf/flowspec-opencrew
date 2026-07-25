from __future__ import annotations

import argparse
import base64
import html
import importlib.util
import json
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


TOOL_ID = "03_02_ShotPlan_QTTSVoiceBuilder"
TOOL_NAME = TOOL_ID
TOOL_VERSION = "1.0.0"
DEFAULT_TTS_PROVIDER = "qwen"
DEFAULT_BASE_MODEL = "qwen3-tts-flash-2025-11-27"
DEFAULT_INSTRUCT_MODEL = "qwen3-tts-instruct-flash-2026-01-26"
DEFAULT_DATABASE_URL_ENV = "OPENCREW_DATABASE_URL"
DEFAULT_OPENCREW_DATABASE_URL = "postgresql://opencrew:opencrew@127.0.0.1:5433/opencrew"
CONFIG_TABLE = "tool_media_provider_configs"
TOOLLIB_ROOT = Path(__file__).resolve().parents[1]
if str(TOOLLIB_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLLIB_ROOT))
from opencrew_runtime_secrets import apply_provider_proxy, resolve_secret_value
QWEN_SHARED_VOICE_GENDERS = {
    "Cherry": "female",
    "Serena": "female",
    "Ethan": "male",
    "Chelsie": "female",
    "Momo": "female",
    "Vivian": "female",
    "Moon": "male",
    "Maia": "female",
    "Kai": "male",
    "Nofish": "male",
    "Bella": "female",
    "Eldric Sage": "male",
    "Mia": "female",
    "Mochi": "male",
    "Bellona": "female",
    "Vincent": "male",
    "Bunny": "female",
    "Neil": "male",
    "Elias": "female",
    "Arthur": "male",
    "Nini": "female",
    "Seren": "female",
    "Pip": "male",
    "Stella": "female",
}
DEFAULT_QWEN_SHARED_VOICES = list(QWEN_SHARED_VOICE_GENDERS)
REQUIRES = ["rebuild_shot_plan.json", "source_package.json", "session_reference_audio"]
OPTIONAL_INPUTS = ["tts/tts_reference_audio_manifest.json"]
PRODUCES = [
    "tts/qtts_voice_builder/qtts_voice_builder_manifest.json",
    "tts/qtts_voice_builder/qtts_voice_builder_review.html",
    "tts/qtts_voice_builder/qtts_voice_builder_selection.json",
    "tts/tts_voice_recommendations.json",
    f"reports/rebuild_v1/{TOOL_ID}.json",
]
SUGGESTED_PREVIOUS_TOOLS = ["02_Rebuild_ShotPlanBuilder", "03_01_ShotPlan_TTSReferenceAudioExtract"]
SUGGESTED_NEXT_TOOLS = ["03_03_ShotPlan_TTSVoiceSelectionWrite"]


class ToolError(RuntimeError):
    pass


def load_gtts_module() -> Any:
    path = Path(__file__).with_name("03_02_ShotPlan_GTTSVoiceBuilder.py")
    spec = importlib.util.spec_from_file_location("_opencrew_gtts_voice_builder", path)
    if not spec or not spec.loader:
        raise ToolError(f"Cannot load shared Gemini builder helpers: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


g = load_gtts_module()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def now_ms() -> int:
    return int(time.time() * 1000)


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "")).strip("_") or "item"


def normalize_database_url(value: str) -> str:
    return value.replace("postgresql+psycopg://", "postgresql://", 1).replace("postgresql+psycopg2://", "postgresql://", 1)


def resolve_database_url(args: argparse.Namespace) -> str:
    env_name = str(args.database_url_env or DEFAULT_DATABASE_URL_ENV)
    return str(args.database_url or "") or os.environ.get(env_name, "") or os.environ.get("DATABASE_URL", "") or DEFAULT_OPENCREW_DATABASE_URL


def decode_db_value(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace").strip()
    if isinstance(value, memoryview):
        return value.tobytes().decode("utf-8", errors="replace").strip()
    return str(value or "").strip()


def load_qwen_tts_key(args: argparse.Namespace) -> str:
    env_key = os.environ.get("OPENCREW_TTS_API_KEY", "").strip()
    env_provider = os.environ.get("OPENCREW_TTS_PROVIDER", "").strip()
    if env_key and env_provider in {"", "qwen", "dashscope"}:
        apply_provider_proxy("qwen")
        return env_key
    try:
        import psycopg  # type: ignore
    except Exception as exc:
        raise ToolError("PostgreSQL driver psycopg is not available and OPENCREW_TTS_API_KEY is not set") from exc
    sql = f"""
SELECT api_key_ref, api_key_ciphertext
FROM {CONFIG_TABLE}
WHERE kind = 'tts' AND provider = 'qwen' AND enabled = TRUE
ORDER BY active DESC
LIMIT 1
"""
    with psycopg.connect(normalize_database_url(resolve_database_url(args)), connect_timeout=8) as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            row = cur.fetchone()
    if not row:
        raise ToolError("No enabled Qwen TTS API key found in tool_media_provider_configs")
    api_key = resolve_secret_value(decode_db_value(row[0]), decode_db_value(row[1] if len(row) > 1 else ""))
    if not api_key:
        raise ToolError("No enabled Qwen TTS API key found in local secret store")
    apply_provider_proxy("qwen")
    return api_key


def post_json_request(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: int = 90) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "Accept": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            body = res.read().decode("utf-8", errors="replace")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:3000]
        raise ToolError(f"HTTP {exc.code}: {detail}") from exc


def first_url(value: Any) -> str:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"url", "audio_url"} and isinstance(item, str) and item.startswith(("http://", "https://")):
                return item
            found = first_url(item)
            if found:
                return found
    if isinstance(value, list):
        for item in value:
            found = first_url(item)
            if found:
                return found
    return ""


def first_audio_data(value: Any) -> str:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"data", "audio_data", "b64_json"} and isinstance(item, str) and len(item) > 100:
                return item
            found = first_audio_data(item)
            if found:
                return found
    if isinstance(value, list):
        for item in value:
            found = first_audio_data(item)
            if found:
                return found
    return ""


def download_binary(url: str, output_path: Path, timeout: int = 120) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"Accept": "audio/*,*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as res:
        output_path.write_bytes(res.read())


def generate_qwen_tts(api_key: str, model: str, voice: str, text: str, instructions: str, output_path: Path) -> dict[str, Any]:
    input_payload: dict[str, Any] = {"text": text, "voice": voice, "language_type": "Chinese"}
    if "instruct" in model and instructions.strip():
        input_payload["instructions"] = instructions.strip()
        input_payload["optimize_instructions"] = True
    result = post_json_request(
        "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation",
        {"model": model, "input": input_payload},
        {"Authorization": f"Bearer {api_key}"},
        timeout=120,
    )
    audio_url = first_url(result)
    if audio_url:
        download_binary(audio_url, output_path)
    else:
        encoded = first_audio_data(result)
        if not encoded:
            raise ToolError(f"Qwen TTS response did not include audio url/data: {json.dumps(result, ensure_ascii=False)[:1200]}")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(base64.b64decode(encoded))
    return {"duration": g.media_duration(output_path), "audio_url": audio_url, "model": model, "voice": voice}


def infer_reference_gender(reference_features: dict[str, Any]) -> str:
    f0 = float(reference_features.get("f0_median") or 0.0)
    voiced_ratio = float(reference_features.get("voiced_ratio") or 0.0)
    if voiced_ratio < 0.2 or f0 <= 0:
        return "unknown"
    if f0 >= 190:
        return "female"
    if f0 <= 165:
        return "male"
    return "unknown"


def resolve_search_voices(args: argparse.Namespace, reference_features: dict[str, Any]) -> tuple[list[str], str, str]:
    explicit = [item.strip() for item in str(args.voices or "").split(",") if item.strip()]
    inferred = infer_reference_gender(reference_features)
    requested = str(args.target_gender or "auto").strip().lower()
    target = inferred if requested == "auto" else requested
    if explicit:
        return explicit, target, inferred
    if target in {"female", "male"}:
        voices = [voice for voice, gender in QWEN_SHARED_VOICE_GENDERS.items() if gender == target]
    else:
        voices = DEFAULT_QWEN_SHARED_VOICES[:]
    return voices, target if target in {"female", "male"} else "all", inferred


def instruct_prompt(parent: dict[str, Any], reference: dict[str, Any], variant: int) -> str:
    parts = parent.get("score_parts") if isinstance(parent.get("score_parts"), dict) else {}
    duration_ratio = float(parts.get("raw_duration") or reference["duration"]) / max(0.1, float(reference["duration"]))
    f0_delta = float(parts.get("f0_median") or 0) - float(reference["f0_median"])
    bright_delta = float(parts.get("centroid") or 0) - float(reference["centroid"])
    instructions = ["年轻中国女性生活短视频口播", "近距离自然收音", "只朗读正文，不添加额外内容"]
    if duration_ratio > 1.15:
        instructions.append("语速更快一点，停顿更短")
    elif duration_ratio < 0.88:
        instructions.append("语速稍慢一点，句子更从容")
    else:
        instructions.append("保持紧凑自然节奏")
    if f0_delta < -25:
        instructions.append("音调略高更年轻")
    elif f0_delta > 25:
        instructions.append("音调略低更稳")
    else:
        instructions.append("中高音区，明亮但不尖")
    if bright_delta > 450:
        instructions.append("音色更柔和，减少刺亮感")
    elif bright_delta < -450:
        instructions.append("音色更清亮，口腔共鸣更靠前")
    else:
        instructions.append("清透温暖")
    if variant == 2:
        instructions.append("情绪更亲切，像给家人准备东西时顺口说明")
    if variant == 3:
        instructions.append("更像手机自拍视频里的真实说话，不要播音腔")
    return "；".join(instructions) + "。"


def evaluate_candidate(
    *,
    api_key: str,
    model: str,
    voice: str,
    text: str,
    instructions: str,
    raw_path: Path,
    candidate_id: str,
    stage: str,
    round_index: int,
    parent_id: str,
    reference_features: dict[str, Any],
    target_duration: float,
    force: bool,
) -> dict[str, Any]:
    if raw_path.exists() and raw_path.stat().st_size > 0 and not force:
        meta = {"duration": g.media_duration(raw_path), "cached": True, "model": model, "voice": voice}
    else:
        meta = generate_qwen_tts(api_key, model, voice, text, instructions, raw_path)
    features = g.audio_features(raw_path)
    score, parts = g.score_candidate(reference_features, features, target_duration)
    return {
        "candidate_id": candidate_id,
        "stage": stage,
        "round": round_index,
        "parent_id": parent_id,
        "provider": DEFAULT_TTS_PROVIDER,
        "model": model,
        "voice": voice,
        "prompt": instructions,
        "prompt_template": instructions,
        "instructions": instructions,
        "raw_audio": str(raw_path),
        "qwen_meta": meta,
        "features": g.summarize_features(features),
        "score": round(float(score), 6),
        "score_parts": {key: round(float(value), 6) for key, value in parts.items()},
        "raw_duration": round(float(features.get("duration") or 0), 3),
    }


def fit_top_candidates(candidates: list[dict[str, Any]], output_dir: Path, target_duration: float, force: bool) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in candidates:
        fit_path = output_dir / "top_fitted" / f"{safe_name(row['candidate_id'])}_fit_{target_duration:.3f}s.wav"
        if fit_path.exists() and fit_path.stat().st_size > 0 and not force:
            fit_meta = {"raw_duration": row.get("raw_duration"), "target_duration": target_duration, "fit_duration": g.media_duration(fit_path), "cached": True}
        else:
            fit_meta = g.fit_audio_to_duration(Path(str(row["raw_audio"])), fit_path, target_duration)
        rows.append({**row, "audio": str(fit_path), "fit_audio": str(fit_path), "fit_meta": fit_meta, "fit_duration": g.media_duration(fit_path)})
    return rows


def build_selection_payload(selection: dict[str, Any], final_candidates: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "tool": TOOL_ID,
        "tool_version": TOOL_VERSION,
        "status": "default_pending_html_confirmation",
        "generated_at": now_ms(),
        "selected_candidate_id": selection.get("candidate_id") or "",
        "default_selection": selection,
        "top_candidates": final_candidates,
    }


def build_compatible_recommendations(plan: dict[str, Any], manifest: dict[str, Any], selection: dict[str, Any]) -> dict[str, Any]:
    recommendations = []
    final_candidates = manifest.get("final_candidates") if isinstance(manifest.get("final_candidates"), list) else []
    for shot in g.shot_list(plan):
        recommendations.append(
            {
                "shot_id": g.shot_id_of(shot),
                "provider": DEFAULT_TTS_PROVIDER,
                "model": selection.get("model") or manifest.get("instruct_model") or DEFAULT_INSTRUCT_MODEL,
                "voice": selection.get("voice") or "",
                "label": selection.get("voice") or "",
                "score": selection.get("score"),
                "stage": selection.get("stage") or "",
                "reason": "global Qwen TTS two-stage voice builder selection from 16s session reference audio",
                "match_source": "qtts_voice_builder",
                "prompt": selection.get("prompt") or "",
                "prompt_template": selection.get("prompt_template") or "",
                "instructions": selection.get("instructions") or "",
                "audio": selection.get("audio") or "",
                "fit_audio": selection.get("fit_audio") or selection.get("audio") or "",
                "raw_audio": selection.get("raw_audio") or "",
                "candidate_id": selection.get("candidate_id") or "",
                "top_candidates": final_candidates,
            }
        )
    return {
        "tool": TOOL_ID,
        "tool_version": TOOL_VERSION,
        "generated_at": now_ms(),
        "status": "completed",
        "recommendations": recommendations,
        "match_result": {
            "provider": DEFAULT_TTS_PROVIDER,
            "base_model": manifest.get("base_model") or DEFAULT_BASE_MODEL,
            "instruct_model": manifest.get("instruct_model") or DEFAULT_INSTRUCT_MODEL,
            "selected_voice": selection.get("voice"),
            "selected_candidate_id": selection.get("candidate_id"),
        },
        "warnings": [],
    }


def build_html_review(output_path: Path, manifest: dict[str, Any], selection_path: Path) -> None:
    candidates = manifest.get("final_candidates") if isinstance(manifest.get("final_candidates"), list) else []
    rows = []
    for index, candidate in enumerate(candidates, start=1):
        audio = g.rel(output_path.parent, candidate.get("audio") or candidate.get("raw_audio") or "")
        prompt = html.escape(str(candidate.get("prompt") or "(no instruct)"))
        parts = candidate.get("score_parts") if isinstance(candidate.get("score_parts"), dict) else {}
        part_rows = "".join(f"<tr><td>{html.escape(str(k))}</td><td>{html.escape(str(v))}</td></tr>" for k, v in parts.items())
        checked = "checked" if candidate.get("candidate_id") == (manifest.get("selection") or {}).get("selected_candidate_id") else ""
        rows.append(
            f"""
<section class="candidate">
  <div class="rank">#{index}</div>
  <div class="main">
    <h2>{html.escape(str(candidate.get('voice') or ''))} <span>{html.escape(str(candidate.get('score') or ''))}</span></h2>
    <p class="meta">Stage: {html.escape(str(candidate.get('stage') or ''))} · Model: {html.escape(str(candidate.get('model') or ''))} · Raw: {html.escape(str(candidate.get('raw_duration') or ''))}s · Fit: {html.escape(str(candidate.get('fit_duration') or ''))}s</p>
    <audio controls src="{html.escape(audio)}"></audio>
    <label><input type="radio" name="defaultCandidate" value="{html.escape(str(candidate.get('candidate_id') or ''))}" {checked}> Use as default</label>
    <h3>Prompt / Instructions</h3>
    <pre>{prompt}</pre>
    <h3>Score Parts</h3>
    <table>{part_rows}</table>
  </div>
</section>
"""
        )
    candidates_json = json.dumps(candidates, ensure_ascii=False)
    html_text = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Qwen TTS Voice Builder Review</title>
  <style>
    body {{ margin: 0; background: #f6f7f9; color: #1f2933; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    header {{ padding: 22px 30px; background: #fff; border-bottom: 1px solid #d9dee7; position: sticky; top: 0; z-index: 2; }}
    h1 {{ margin: 0 0 6px; font-size: 24px; }}
    .summary {{ margin: 0; color: #52606d; }}
    .actions {{ margin-top: 14px; display: flex; gap: 10px; flex-wrap: wrap; }}
    button {{ border: 1px solid #1f6feb; background: #1f6feb; color: #fff; border-radius: 6px; padding: 9px 12px; cursor: pointer; }}
    button.secondary {{ background: #fff; color: #1f2933; border-color: #cbd2d9; }}
    main {{ padding: 20px 30px 44px; max-width: 1080px; }}
    .candidate {{ display: grid; grid-template-columns: 52px minmax(0, 1fr); gap: 16px; padding: 18px 0; border-bottom: 1px solid #d9dee7; }}
    .rank {{ font-size: 22px; font-weight: 700; color: #1f6feb; }}
    h2 {{ margin: 0 0 5px; font-size: 19px; }}
    h2 span {{ color: #52606d; font-size: 15px; }}
    .meta {{ margin: 0 0 10px; color: #52606d; }}
    audio {{ width: 100%; max-width: 580px; display: block; margin-bottom: 10px; }}
    pre {{ white-space: pre-wrap; background: #fff; border: 1px solid #d9dee7; padding: 12px; border-radius: 6px; line-height: 1.5; }}
    table {{ border-collapse: collapse; background: #fff; }}
    td {{ border: 1px solid #d9dee7; padding: 5px 8px; }}
    @media (max-width: 680px) {{ header, main {{ padding-left: 16px; padding-right: 16px; }} .candidate {{ grid-template-columns: 1fr; }} }}
  </style>
</head>
<body>
<header>
  <h1>Qwen TTS Voice Builder</h1>
  <p class="summary">Reference: {html.escape(str((manifest.get('reference_clip') or {{}}).get('clip_audio') or ''))} · Base: {html.escape(str(manifest.get('base_model') or ''))} · Instruct: {html.escape(str(manifest.get('instruct_model') or ''))}</p>
  <div class="actions">
    <button id="copySelection">Copy JSON</button>
    <button id="downloadSelection" class="secondary">Download JSON</button>
    <span id="status"></span>
  </div>
</header>
<main>
{''.join(rows)}
</main>
<script>
const candidates = {candidates_json};
const selectionPath = {json.dumps(g.rel(output_path.parent, selection_path), ensure_ascii=False)};
function selectedPayload() {{
  const selectedId = document.querySelector('input[name="defaultCandidate"]:checked')?.value || "";
  const selected = candidates.find(item => item.candidate_id === selectedId) || candidates[0] || null;
  return {{
    tool: {json.dumps(TOOL_ID)},
    tool_version: {json.dumps(TOOL_VERSION)},
    status: "html_confirmed",
    generated_at: Date.now(),
    selection_path: selectionPath,
    selected_candidate_id: selected ? selected.candidate_id : "",
    default_selection: selected,
    top_candidates: candidates
  }};
}}
function textPayload() {{ return JSON.stringify(selectedPayload(), null, 2); }}
document.getElementById('copySelection').addEventListener('click', async () => {{
  await navigator.clipboard.writeText(textPayload());
  document.getElementById('status').textContent = 'Copied';
}});
document.getElementById('downloadSelection').addEventListener('click', () => {{
  const blob = new Blob([textPayload()], {{type: 'application/json'}});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'qtts_voice_builder_selection.json';
  a.click();
  URL.revokeObjectURL(a.href);
}});
</script>
</body>
</html>
"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html_text, encoding="utf-8")


def optimize(workspace: Path, args: argparse.Namespace) -> dict[str, Any]:
    plan = g.read_json(workspace / args.input)
    source_package = g.read_json(workspace / args.source_package)
    reference_manifest = g.load_reference_audio_manifest(workspace)
    reference_audio, reference_source, reference_candidates = g.resolve_reference_audio(workspace, source_package, reference_manifest, args.reference_audio)
    if not reference_audio:
        raise ToolError("No session reference audio found. Run 03_01_ShotPlan_TTSReferenceAudioExtract first or pass --reference-audio.")

    output_dir = workspace / args.output_dir
    target_duration = float(args.reference_duration)
    reference_clip_path = output_dir / "reference" / f"reference_{float(args.reference_start):.3f}_{target_duration:.3f}s.wav"
    reference_clip = g.extract_reference_clip(reference_audio, reference_clip_path, float(args.reference_start), target_duration, bool(args.force))
    reference_features = g.audio_features(reference_clip_path)
    text, text_sources, sample_srt = g.sample_text_for_time_range(plan, float(args.reference_start), target_duration)
    if not text:
        raise ToolError("No TTS sample text found in rebuild_shot_plan.json")

    api_key = load_qwen_tts_key(args)
    voices, target_gender, inferred_gender = resolve_search_voices(args, reference_features)
    stage1_rows: list[dict[str, Any]] = []
    for voice in voices:
        row = evaluate_candidate(
            api_key=api_key,
            model=args.base_model,
            voice=voice,
            text=text,
            instructions="",
            raw_path=output_dir / "stage_01_no_instruct" / f"r1_{safe_name(voice)}_no_instruct.wav",
            candidate_id=f"r1_{safe_name(voice)}_no_instruct",
            stage="no_instruct",
            round_index=1,
            parent_id="",
            reference_features=reference_features,
            target_duration=target_duration,
            force=bool(args.force),
        )
        stage1_rows.append(row)

    stage1_top = sorted(stage1_rows, key=lambda item: float(item.get("score") or 0), reverse=True)[: int(args.top_k)]
    stage2_rows: list[dict[str, Any]] = []
    for parent in stage1_top:
        for variant in range(1, int(args.instruct_variants) + 1):
            instructions = instruct_prompt(parent, reference_features, variant)
            voice = str(parent.get("voice") or "")
            row = evaluate_candidate(
                api_key=api_key,
                model=args.instruct_model,
                voice=voice,
                text=text,
                instructions=instructions,
                raw_path=output_dir / "stage_02_instruct" / f"r2_{safe_name(voice)}_v{variant}.wav",
                candidate_id=f"r2_{safe_name(voice)}_v{variant}",
                stage="instruct_refine",
                round_index=2,
                parent_id=str(parent.get("candidate_id") or ""),
                reference_features=reference_features,
                target_duration=target_duration,
                force=bool(args.force),
            )
            stage2_rows.append(row)

    stage2_top: list[dict[str, Any]] = []
    for parent in stage1_top:
        parent_id = str(parent.get("candidate_id") or "")
        variants = [row for row in stage2_rows if str(row.get("parent_id") or "") == parent_id]
        if variants:
            stage2_top.append(max(variants, key=lambda item: float(item.get("score") or 0)))
    final_candidates = fit_top_candidates(stage1_top + stage2_top, output_dir, target_duration, bool(args.force))
    selection = max(final_candidates, key=lambda item: float(item.get("score") or 0)) if final_candidates else {}
    selection_payload = build_selection_payload(selection, final_candidates)
    selection_path = output_dir / "qtts_voice_builder_selection.json"
    write_json(selection_path, selection_payload)

    manifest = {
        "tool": TOOL_ID,
        "tool_version": TOOL_VERSION,
        "generated_at": now_ms(),
        "status": "completed",
        "workspace": str(workspace),
        "provider": DEFAULT_TTS_PROVIDER,
        "base_model": args.base_model,
        "instruct_model": args.instruct_model,
        "voices": voices,
        "target_gender": target_gender,
        "inferred_reference_gender": inferred_gender,
        "voice_search_scope": "explicit_cli_voices" if str(args.voices or "").strip() else "qwen_shared_instruct_compatible_gender_filtered",
        "voice_gender_map": {voice: QWEN_SHARED_VOICE_GENDERS.get(voice, "unknown") for voice in voices},
        "reference_audio": str(reference_audio),
        "reference_source": reference_source,
        "reference_candidates": reference_candidates,
        "reference_clip": reference_clip,
        "reference_features": g.summarize_features(reference_features),
        "sample_text": text,
        "sample_srt": sample_srt,
        "sample_text_sources": text_sources,
        "target_duration": target_duration,
        "stage1_top_candidates": stage1_top,
        "stage2_top_candidates": stage2_top,
        "final_candidates": final_candidates,
        "all_candidates": sorted(stage1_rows + stage2_rows, key=lambda item: float(item.get("score") or 0), reverse=True),
        "selection": selection_payload,
    }
    manifest_path = output_dir / "qtts_voice_builder_manifest.json"
    write_json(manifest_path, manifest)
    if args.generate_html:
        html_path = output_dir / "qtts_voice_builder_review.html"
        build_html_review(html_path, manifest, selection_path)
        manifest["html_review"] = str(html_path)
        write_json(manifest_path, manifest)
    write_json(workspace / "tts" / "tts_voice_recommendations.json", build_compatible_recommendations(plan, manifest, selection))
    write_json(workspace / "reports" / "rebuild_v1" / f"{TOOL_ID}.json", manifest)
    return manifest


def check_dependencies(workspace: Path, args: argparse.Namespace) -> dict[str, Any]:
    satisfied: list[str] = []
    missing: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    plan_path = workspace / args.input
    source_path = workspace / args.source_package
    if plan_path.exists():
        satisfied.append("rebuild_shot_plan.json")
    else:
        missing.append({"dependency": "rebuild_shot_plan.json", "reason": f"required workspace file does not exist: {args.input}", "suggested_tools": ["02_Rebuild_ShotPlanBuilder"]})
    source_package: dict[str, Any] = {}
    if source_path.exists():
        satisfied.append("source_package.json")
        try:
            payload = g.read_json(source_path)
            source_package = payload if isinstance(payload, dict) else {}
        except Exception as exc:
            missing.append({"dependency": "source_package.json", "reason": f"failed to read source package: {exc}", "suggested_tools": ["01_Rebuild_SourcePackageLoad"]})
    else:
        missing.append({"dependency": "source_package.json", "reason": f"required workspace file does not exist: {args.source_package}", "suggested_tools": ["01_Rebuild_SourcePackageLoad"]})
    manifest = g.load_reference_audio_manifest(workspace)
    reference_audio, _source, candidates = g.resolve_reference_audio(workspace, source_package, manifest, args.reference_audio)
    if reference_audio:
        satisfied.append("session_reference_audio")
    else:
        missing.append({"dependency": "session_reference_audio", "reason": "no reference audio found in tts manifest or source analysis workspace", "suggested_tools": ["03_01_ShotPlan_TTSReferenceAudioExtract"], "candidates": candidates})
    if not (workspace / "tts" / "tts_reference_audio_manifest.json").exists():
        warnings.append({"dependency": "tts/tts_reference_audio_manifest.json", "reason": "optional manifest is absent; tool will fall back to source_package.source.analysis_workspace/audio/reference_audio.wav"})
    for name in ("ffmpeg", "ffprobe"):
        binary = g.find_binary(name)
        if not shutil.which(binary) and not Path(binary).exists():
            missing.append({"dependency": name, "reason": f"required media binary not found: {name}", "suggested_tools": []})
        else:
            satisfied.append(name)
    return {"status": "blocked" if missing else "warning" if warnings else "satisfied", "satisfied": satisfied, "missing": missing, "warnings": warnings}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=f"Standalone Rebuild_V1 tool: {TOOL_ID}")
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--task-id", type=int, default=0)
    parser.add_argument("--session-id", type=int, default=0)
    parser.add_argument("--input", default="rebuild_shot_plan.json")
    parser.add_argument("--source-package", default="source_package.json")
    parser.add_argument("--output-dir", default="tts/qtts_voice_builder")
    parser.add_argument("--database-url", default="")
    parser.add_argument("--database-url-env", default=DEFAULT_DATABASE_URL_ENV)
    parser.add_argument("--reference-audio", default="")
    parser.add_argument("--reference-start", type=float, default=0.0)
    parser.add_argument("--reference-duration", type=float, default=16.0)
    parser.add_argument("--base-model", default=DEFAULT_BASE_MODEL)
    parser.add_argument("--instruct-model", default=DEFAULT_INSTRUCT_MODEL)
    parser.add_argument("--voices", default="", help="Comma-separated voice override. Empty means all instruct-compatible Qwen shared voices filtered by --target-gender.")
    parser.add_argument("--target-gender", choices=["auto", "female", "male", "all"], default="auto")
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--instruct-variants", type=int, default=3)
    parser.add_argument("--generate-html", dest="generate_html", action="store_true", default=True)
    parser.add_argument("--no-generate-html", dest="generate_html", action="store_false")
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
            status, result = ("blocked" if dependencies["missing"] else "completed_with_warnings" if dependencies["warnings"] else "completed"), None
        else:
            result = optimize(workspace, args)
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
