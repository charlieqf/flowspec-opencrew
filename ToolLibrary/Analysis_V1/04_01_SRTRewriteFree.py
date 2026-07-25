from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import sys
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _load_strict_module() -> Any:
    path = Path(__file__).resolve().with_name("04_01_SRTRewrite.py")
    spec = importlib.util.spec_from_file_location("analysis_v1_srt_rewrite_strict_shared", path)
    if not spec or not spec.loader:
        raise RuntimeError(f"Cannot load strict rewrite helpers from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


strict = _load_strict_module()

TOOL_NAME = "04_01_SRTRewriteFree"
TOOL_VERSION = "0.1.0"
CONTEXT_DIR_NAME = "SessionContext"
VARIABLES_REL = f"{CONTEXT_DIR_NAME}/Variables.json"
TOOL_DIR_NAME = "S6_04_01_SRTRewriteFree"
WORKING_VARIABLES_REL = f"{TOOL_DIR_NAME}/Working/InputFrom_0_Variables.json"
WORKING_FINAL_ITEMS_REL = f"{TOOL_DIR_NAME}/Working/InputFrom_4_final_srt_frame_items.json"
WORKING_TIMING_PROFILE_REL = f"{TOOL_DIR_NAME}/Working/InputTiming_tts_reference_profile.json"
PROMPT_DIR_REL = f"{TOOL_DIR_NAME}/Prompt"
REWRITE_PROMPT_REL = f"{PROMPT_DIR_REL}/00_srt_rewrite_free_prompt.md"
OUTPUT_MODEL_RESPONSE_REL = f"{TOOL_DIR_NAME}/Output/model_response.json"
OUTPUT_REWRITTEN_ITEMS_REL = f"{TOOL_DIR_NAME}/Output/rewritten_srt_items.json"
OUTPUT_REWRITTEN_SRT_REL = f"{TOOL_DIR_NAME}/Output/rewritten_dialogue.srt"
OUTPUT_MANIFEST_REL = f"{TOOL_DIR_NAME}/Output/OutputManifest.json"
REPORT_RESULT_REL = f"{TOOL_DIR_NAME}/Report/Result.json"
SESSION_FINAL_ITEMS_REL = "SessionOutput/subtitle/final_srt_frame_items.json"
SESSION_TTS_CANDIDATES_REL = "SessionOutput/tts/tts_builder_candidates.json"
SESSION_REWRITTEN_ITEMS_REL = "SessionOutput/subtitle/rewritten_srt_items.json"
SESSION_REWRITTEN_SRT_REL = "SessionOutput/subtitle/rewritten_dialogue.srt"
DEFAULT_SECONDS_PER_CHARACTER = 0.18
SECRET_PATTERNS = strict.SECRET_PATTERNS


class BlockedError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class ToolError(RuntimeError):
    pass


@dataclass(frozen=True)
class Args:
    workspace: str
    model_provider: str
    model_id: str
    force: bool
    resume: bool
    force_regenerate_prompts: bool
    print_json: bool


@dataclass(frozen=True)
class ModelConfig:
    provider: str
    model: str
    source: str


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def now_ms() -> int:
    return int(time.time() * 1000)


def json_safe(value: Any) -> Any:
    return strict.json_safe(value)


def text_value(value: Any) -> str:
    return strict.text_value(value)


def dict_value(value: Any) -> dict[str, Any]:
    return strict.dict_value(value)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(json_safe(payload), ensure_ascii=False, indent=2), encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_text_if_needed(path: Path, text: str, force: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text(encoding="utf-8").strip() and not force:
        return
    path.write_text(text, encoding="utf-8")


def remove_path(path: Path) -> None:
    if not path.exists():
        return
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def relpath(path: Path | str, workspace: Path) -> str:
    return strict.relpath(path, workspace)


def resolve_workspace(raw_workspace: str) -> Path:
    return strict.resolve_workspace(raw_workspace)


def validate_workspace(workspace: Path) -> None:
    strict.validate_workspace(workspace)


def load_variables(workspace: Path) -> dict[str, Any]:
    path = workspace / VARIABLES_REL
    if not path.exists():
        raise BlockedError("variables_missing", f"Required SessionContext file is missing: {VARIABLES_REL}.")
    payload = read_json(path)
    if not isinstance(payload, dict):
        raise BlockedError("variables_invalid", f"{VARIABLES_REL} must contain a JSON object.")
    return payload


def load_final_items(workspace: Path) -> dict[str, Any]:
    path = workspace / SESSION_FINAL_ITEMS_REL
    if not path.exists():
        raise BlockedError("final_srt_frame_items_missing", f"Required final SRT frame JSON is missing: {SESSION_FINAL_ITEMS_REL}. Run 02_02_VideoSRTFrame.py first.")
    payload = read_json(path)
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise BlockedError("final_srt_frame_items_invalid", f"{SESSION_FINAL_ITEMS_REL} must contain a JSON object with items.")
    if not payload["items"]:
        raise BlockedError("final_srt_frame_items_empty", f"{SESSION_FINAL_ITEMS_REL} contains no dialogue items.")
    return payload


def ensure_dirs(workspace: Path) -> None:
    for rel in (
        f"{TOOL_DIR_NAME}/Working",
        f"{TOOL_DIR_NAME}/Output",
        PROMPT_DIR_REL,
        f"{TOOL_DIR_NAME}/Report",
        "SessionOutput/subtitle",
    ):
        (workspace / rel).mkdir(parents=True, exist_ok=True)


def base_result(workspace: Path, args: Args) -> dict[str, Any]:
    return {
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "status": "completed",
        "workspace_dir": str(workspace),
        "requires_database": False,
        "prepared_directories": [],
        "inputs": {},
        "outputs": {},
        "counts": {},
        "warnings": [],
        "blocked_reasons": [],
        "created_files": [],
        "started_at": now_iso(),
        "updated_at": now_iso(),
        "force": bool(args.force),
        "resume": bool(args.resume),
    }


def add_block(result: dict[str, Any], code: str, message: str) -> None:
    result["status"] = "blocked"
    result.setdefault("blocked_reasons", []).append({"code": code, "message": message})
    result["message"] = message


def force_reset(workspace: Path, result: dict[str, Any]) -> None:
    for rel in (
        f"{TOOL_DIR_NAME}/Working",
        f"{TOOL_DIR_NAME}/Output",
        PROMPT_DIR_REL,
        f"{TOOL_DIR_NAME}/Report",
        SESSION_REWRITTEN_ITEMS_REL,
        SESSION_REWRITTEN_SRT_REL,
    ):
        path = workspace / rel
        if path.exists():
            remove_path(path)
            result.setdefault("cleanup_actions", []).append({"path": rel, "action": "removed"})


def business_context(variables: dict[str, Any]) -> dict[str, str]:
    return strict.business_context(variables)


def resolve_rewrite_prompt(variables: dict[str, Any]) -> tuple[str, str]:
    rewrite = dict_value(variables.get("rewrite_prompt"))
    prompt = text_value(rewrite.get("final_prompt")) or text_value(variables.get("rewrite_final_prompt")) or text_value(variables.get("final_prompt"))
    source = (
        text_value(rewrite.get("source"))
        or ("SessionContext/Variables.json:rewrite_prompt.final_prompt" if text_value(rewrite.get("final_prompt")) else "")
        or ("SessionContext/Variables.json:rewrite_final_prompt" if text_value(variables.get("rewrite_final_prompt")) else "")
        or ("SessionContext/Variables.json:final_prompt" if text_value(variables.get("final_prompt")) else "")
    )
    if not prompt:
        raise BlockedError("rewrite_final_prompt_missing", "04_01_SRTRewriteFree requires rewrite_prompt.final_prompt. The legacy final_prompt fallback is also empty.")
    return prompt, source


def resolve_model_config(args: Args, variables: dict[str, Any]) -> ModelConfig:
    rewrite_config = dict_value(variables.get("rewrite_model_config"))
    provider = text_value(args.model_provider) or text_value(rewrite_config.get("provider")) or text_value(variables.get("run_model_provider"))
    model = text_value(args.model_id) or text_value(rewrite_config.get("model")) or text_value(rewrite_config.get("model_id")) or text_value(variables.get("run_model_id"))
    if not provider or not model:
        raise BlockedError(
            "text_model_config_missing",
            "04_01_SRTRewriteFree requires a run model. Provide --model-provider/--model-id or set run_model_provider/run_model_id in SessionContext/Variables.json.",
        )
    return ModelConfig(provider=provider, model=model, source="cli" if args.model_provider or args.model_id else "SessionContext/Variables.json:run_model")


def minimal_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "srt_id": text_value(item.get("srt_id")),
        "dialogue": text_value(item.get("dialogue")),
        "start": item.get("start"),
        "end": item.get("end"),
        "duration": item.get("duration"),
    }


def build_rewrite_prompt(variables: dict[str, Any], source_items: list[dict[str, Any]], rewrite_final_prompt: str, prompt_source: str) -> str:
    context = business_context(variables)
    input_payload = {"items": [minimal_item(item) for item in source_items]}
    return f"""# SRT Free Rewrite Prompt

## Rewrite Final Prompt
{rewrite_final_prompt}

## Prompt Source
{prompt_source}

## Business Context
{json.dumps(context, ensure_ascii=False, indent=2)}

## Task
请基于 Rewrite Final Prompt 和 Input Items，重新生成一版可用于口播的 rewritten SRT。

## Free Rewrite Mode
1. 完全遵从 Rewrite Final Prompt 的业务要求。
2. 可以改写、合并、拆分、新增、删除或重排句子。
3. 不要求输出句数等于输入句数。
4. 不要求沿用输入 srt_id。
5. 不要求逐句一一对应原 SRT。
6. 输出顺序就是后续 StoryBoard 的消费顺序。
7. 输出 dialogue 必须使用简体中文；英文、数字、品牌名可按提示词要求保留。
8. 不要输出图片路径；时间将由工具根据 TTS 参考语速计算。

## Input Items
{json.dumps(input_payload, ensure_ascii=False, indent=2)}

## Required Output JSON
只输出严格 JSON，不要 Markdown，不要解释，不要代码块。

{{
  "items": [
    {{
      "dialogue": "新的第一句口播",
      "note": "可选，说明这一句的表达功能"
    }}
  ]
}}
"""


def model_items(response: dict[str, Any]) -> list[dict[str, Any]]:
    return strict.model_items(response)


def clean_dialogue(value: Any) -> str:
    return strict.clean_dialogue(value)


def normalize_model_response_to_simplified(response: dict[str, Any]) -> int:
    return strict.normalize_model_response_to_simplified(response)


def record_simplified_normalization(result: dict[str, Any], changed: int) -> None:
    return strict.record_simplified_normalization(result, changed)


def effective_char_count(value: Any) -> int:
    text = str(value or "")
    cleaned = re.sub(r"[\s\u3000]+", "", text)
    cleaned = re.sub(r"[，。！？、；：,.!?;:\"'“”‘’（）()\[\]【】《》<>…—\-_/\\|·~`]", "", cleaned)
    return max(1, len(cleaned))


def item_start(item: dict[str, Any]) -> float:
    try:
        return float(item.get("start") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def item_end(item: dict[str, Any]) -> float:
    try:
        end = float(item.get("end") or 0.0)
    except (TypeError, ValueError):
        end = 0.0
    try:
        duration = float(item.get("duration") or 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    return max(end, item_start(item) + max(0.0, duration))


def selected_dialogue_for_range(source_items: list[dict[str, Any]], start: float, end: float) -> str:
    texts = []
    for item in sorted(source_items, key=item_start):
        if item_end(item) > start and item_start(item) < end:
            dialogue = text_value(item.get("dialogue"))
            if dialogue:
                texts.append(dialogue)
    return "".join(texts)


def variable_default_seconds_per_character(variables: dict[str, Any]) -> float:
    for key in ("free_rewrite_seconds_per_character", "rewrite_seconds_per_character", "tts_seconds_per_character"):
        try:
            value = float(variables.get(key) or 0.0)
        except (TypeError, ValueError):
            value = 0.0
        if value > 0:
            return value
    return DEFAULT_SECONDS_PER_CHARACTER


def resolve_timing_profile(workspace: Path, variables: dict[str, Any], source_items: list[dict[str, Any]]) -> dict[str, Any]:
    candidates_path = workspace / SESSION_TTS_CANDIDATES_REL
    if candidates_path.exists():
        try:
            candidates = read_json(candidates_path)
            sample_policy = dict_value(candidates.get("sample_policy"))
            selected_range = dict_value(sample_policy.get("selected_range"))
            start = float(selected_range.get("start") or 0.0)
            end = float(selected_range.get("end") or 0.0)
            duration = float(sample_policy.get("selected_duration") or max(0.0, end - start))
            if duration > 0:
                text = selected_dialogue_for_range(source_items, start, start + duration)
                chars = effective_char_count(text)
                return {
                    "source": SESSION_TTS_CANDIDATES_REL,
                    "policy": "tts_reference_seconds_per_character",
                    "reference_start": round(start, 3),
                    "reference_end": round(start + duration, 3),
                    "reference_duration": round(duration, 3),
                    "reference_char_count": chars,
                    "seconds_per_character": duration / max(1, chars),
                }
        except Exception:
            pass
    fallback = variable_default_seconds_per_character(variables)
    return {
        "source": "default",
        "policy": "default_seconds_per_character",
        "seconds_per_character": fallback,
        "timing_warning": "tts_reference_missing_default_seconds_per_character_used",
    }


def normalize_srt_id(value: str, fallback: str, seen: set[str]) -> str:
    candidate = re.sub(r"[^A-Za-z0-9_\-]", "_", value.strip()) if value.strip() else fallback
    candidate = candidate.strip("_") or fallback
    if candidate in seen:
        candidate = fallback
    base = candidate
    suffix = 2
    while candidate in seen:
        candidate = f"{base}_{suffix}"
        suffix += 1
    seen.add(candidate)
    return candidate


def normalize_free_items(response: dict[str, Any], timing_profile: dict[str, Any]) -> list[dict[str, Any]]:
    raw_items = model_items(response)
    if not raw_items:
        raise BlockedError("srt_rewrite_free_model_output_empty", "Model output must contain a non-empty items array.")
    seconds_per_character = float(timing_profile.get("seconds_per_character") or DEFAULT_SECONDS_PER_CHARACTER)
    seen: set[str] = set()
    start = 0.0
    merged: list[dict[str, Any]] = []
    for index, row in enumerate(raw_items, start=1):
        dialogue = strict.to_simplified_chinese(clean_dialogue(row.get("rewritten_dialogue") or row.get("dialogue") or row.get("new_text") or row.get("text")))
        if not dialogue:
            raise BlockedError("srt_rewrite_free_dialogue_missing", f"Output item #{index} has empty dialogue.")
        srt_id = normalize_srt_id(text_value(row.get("srt_id")), f"free_srt_{index:04d}", seen)
        chars = effective_char_count(dialogue)
        duration = max(0.01, seconds_per_character * chars)
        end = start + duration
        merged.append({
            "srt_id": srt_id,
            "order": index,
            "dialogue": dialogue,
            "start": round(start, 3),
            "end": round(end, 3),
            "duration": round(duration, 3),
            "char_count": chars,
            "timing_source": str(timing_profile.get("policy") or "tts_reference_seconds_per_character"),
            **({"note": text_value(row.get("note"))} if text_value(row.get("note")) else {}),
        })
        start = end
    return merged


def build_srt(items: list[dict[str, Any]]) -> str:
    return strict.build_srt(items)


def build_output_manifest(workspace: Path, files: list[tuple[str, str]]) -> dict[str, Any]:
    rows = []
    for rel, kind in files:
        path = workspace / rel
        if not path.is_file():
            continue
        data = path.read_bytes()
        rows.append({
            "path": rel,
            "kind": kind,
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "visibility": "internal",
            "downloadable": True,
            "sensitivity": "standard",
            "schema_name": "analysis_v1_rewritten_srt_items_free" if rel.endswith(".json") and "rewritten_srt_items" in rel else "",
        })
    return {
        "schema_version": "1.0",
        "tool_id": "04_01_free",
        "tool_name": TOOL_NAME,
        "step_id": TOOL_DIR_NAME,
        "status": "completed",
        "files": rows,
        "created_at": now_iso(),
    }


def build_final_payload(variables: dict[str, Any], prompt_source: str, config: ModelConfig, source_items: list[dict[str, Any]], merged_items: list[dict[str, Any]], timing_profile: dict[str, Any]) -> dict[str, Any]:
    rewrite_final_prompt, _ = resolve_rewrite_prompt(variables)
    payload = {
        "schema_version": "analysis_v1_rewritten_srt_items_free_0.1",
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "rewrite_mode": "free",
        "source_items_path": SESSION_FINAL_ITEMS_REL,
        "prompt_source": prompt_source,
        "rewrite_final_prompt_sha256": hashlib.sha256(rewrite_final_prompt.encode("utf-8")).hexdigest(),
        "business_context": business_context(variables),
        "model": {"provider": config.provider, "model": config.model, "source": config.source},
        "identity_policy": "free_rewrite_new_srt_ids_timing_from_tts_reference",
        "timing_policy": timing_profile.get("policy"),
        "timing_profile": timing_profile,
        "items": merged_items,
        "created_at": now_iso(),
    }
    if timing_profile.get("timing_warning"):
        payload["timing_warning"] = timing_profile["timing_warning"]
    return payload


def parse_json_from_text(text: str) -> dict[str, Any]:
    return strict.parse_json_from_text(text)


def resolve_opencode_runtime(variables: dict[str, Any]) -> dict[str, str]:
    runtime = dict_value(variables.get("opencode_runtime"))
    base_url = (
        text_value(runtime.get("base_url"))
        or text_value(variables.get("opencode_base_url"))
        or os.environ.get("OPENCREW_OPENCODE_BASE_URL", "").strip()
        or os.environ.get("OPENCODE_BASE_URL", "").strip()
    ).rstrip("/")
    username = (
        text_value(runtime.get("auth_username"))
        or text_value(runtime.get("username"))
        or text_value(variables.get("opencode_auth_username"))
        or os.environ.get("OPENCREW_OPENCODE_USERNAME", "").strip()
        or os.environ.get("OPENCODE_USERNAME", "").strip()
    )
    password = (
        text_value(runtime.get("auth_password"))
        or text_value(runtime.get("password"))
        or text_value(variables.get("opencode_auth_password"))
        or os.environ.get("OPENCREW_OPENCODE_PASSWORD", "").strip()
        or os.environ.get("OPENCODE_PASSWORD", "").strip()
    )
    if not base_url or not username or not password:
        raise BlockedError(
            "opencode_runtime_missing",
            "04_01_SRTRewriteFree requires OpenCode runtime from SessionContext/Variables.json or OPENCREW_OPENCODE_BASE_URL/USERNAME/PASSWORD; it does not read the database.",
        )
    return {"base_url": base_url, "username": username, "password": password}


def opencode_request(runtime: dict[str, str], method: str, path: str, payload: dict[str, Any] | None, directory: str, timeout: int = 120) -> Any:
    return strict.opencode_request(runtime, method, path, payload, directory, timeout)


def last_completed_assistant(messages: list[dict[str, Any]], started_after: int) -> str:
    return strict.last_completed_assistant(messages, started_after)


def call_opencode_run_model(args: Args, variables: dict[str, Any], config: ModelConfig, prompt_path: Path) -> dict[str, Any]:
    session_id = text_value(variables.get("opencode_session_id"))
    if not session_id:
        raise BlockedError("opencode_session_id_missing", "SessionContext/Variables.json is missing opencode_session_id.")
    directory = text_value(variables.get("workspace_dir")) or str(prompt_path.parents[2])
    runtime = resolve_opencode_runtime(variables)
    prompt_text = prompt_path.read_text(encoding="utf-8")
    payload = {
        "parts": [{"type": "text", "text": prompt_text}],
        "model": {"providerID": config.provider, "modelID": config.model},
    }
    started_at = now_ms()
    opencode_request(runtime, "POST", f"/session/{urllib.parse.quote(session_id, safe='')}/prompt_async", payload, directory, timeout=30)
    deadline = time.time() + 300
    while time.time() < deadline:
        messages = opencode_request(runtime, "GET", f"/session/{urllib.parse.quote(session_id, safe='')}/message", None, directory, timeout=30) or []
        assistant_text = last_completed_assistant(messages, started_at)
        if assistant_text:
            return parse_json_from_text(assistant_text)
        time.sleep(1)
    raise ToolError("OpenCode run model timed out before returning a completed assistant message.")


def call_text_model_with_variables(args: Args, variables: dict[str, Any], config: ModelConfig, prompt_path: Path) -> dict[str, Any]:
    return call_opencode_run_model(args, variables, config, prompt_path)


def run_rewrite(workspace: Path, args: Args, variables: dict[str, Any], final_items_payload: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    source_items = [item for item in final_items_payload.get("items", []) if isinstance(item, dict)]
    rewrite_final_prompt, prompt_source = resolve_rewrite_prompt(variables)
    if strict.wants_original_dialogue_passthrough(variables):
        strict.validate_input_items(source_items)
        merged_items = strict.original_dialogue_passthrough_items(source_items)
        final_payload = strict.build_original_dialogue_passthrough_payload(
            variables,
            prompt_source,
            source_items,
            merged_items,
            tool_name=TOOL_NAME,
            tool_version=TOOL_VERSION,
        )
        write_json(workspace / OUTPUT_REWRITTEN_ITEMS_REL, final_payload)
        write_text(workspace / OUTPUT_REWRITTEN_SRT_REL, build_srt(merged_items))
        manifest = build_output_manifest(workspace, [
            (OUTPUT_REWRITTEN_ITEMS_REL, "subtitle"),
            (OUTPUT_REWRITTEN_SRT_REL, "subtitle"),
        ])
        write_json(workspace / OUTPUT_MANIFEST_REL, manifest)
        write_json(workspace / SESSION_REWRITTEN_ITEMS_REL, final_payload)
        write_text(workspace / SESSION_REWRITTEN_SRT_REL, build_srt(merged_items))

        result["status"] = "completed"
        result["inputs"] = {
            "variables": VARIABLES_REL,
            "final_srt_frame_items": SESSION_FINAL_ITEMS_REL,
        }
        result["outputs"] = {
            "tool_rewritten_srt_items": OUTPUT_REWRITTEN_ITEMS_REL,
            "rewritten_srt_items": SESSION_REWRITTEN_ITEMS_REL,
            "rewritten_dialogue_srt": SESSION_REWRITTEN_SRT_REL,
            "output_manifest": OUTPUT_MANIFEST_REL,
        }
        result["counts"] = {
            "input_items": len(source_items),
            "rewritten_items": len(merged_items),
            "passthrough_original_dialogue": 1,
        }
        result["warnings"].append({
            "code": "original_dialogue_passthrough",
            "message": "Rewrite prompt requested the original video script, so rewritten SRT was copied from the original SRT without a model rewrite.",
        })
        result["created_files"] = [
            WORKING_VARIABLES_REL,
            WORKING_FINAL_ITEMS_REL,
            OUTPUT_REWRITTEN_ITEMS_REL,
            OUTPUT_REWRITTEN_SRT_REL,
            OUTPUT_MANIFEST_REL,
            SESSION_REWRITTEN_ITEMS_REL,
            SESSION_REWRITTEN_SRT_REL,
            REPORT_RESULT_REL,
        ]
        return final_payload
    config = resolve_model_config(args, variables)
    timing_profile = resolve_timing_profile(workspace, variables, source_items)
    write_json(workspace / WORKING_TIMING_PROFILE_REL, timing_profile)

    prompt_path = workspace / REWRITE_PROMPT_REL
    write_text_if_needed(prompt_path, build_rewrite_prompt(variables, source_items, rewrite_final_prompt, prompt_source), args.force_regenerate_prompts or args.force)
    response = call_text_model_with_variables(args, variables, config, prompt_path)
    record_simplified_normalization(result, normalize_model_response_to_simplified(response))
    write_json(workspace / OUTPUT_MODEL_RESPONSE_REL, response)

    merged_items = normalize_free_items(response, timing_profile)
    final_payload = build_final_payload(variables, prompt_source, config, source_items, merged_items, timing_profile)
    write_json(workspace / OUTPUT_REWRITTEN_ITEMS_REL, final_payload)
    write_text(workspace / OUTPUT_REWRITTEN_SRT_REL, build_srt(merged_items))
    manifest = build_output_manifest(workspace, [
        (OUTPUT_MODEL_RESPONSE_REL, "model_response"),
        (OUTPUT_REWRITTEN_ITEMS_REL, "subtitle"),
        (OUTPUT_REWRITTEN_SRT_REL, "subtitle"),
    ])
    write_json(workspace / OUTPUT_MANIFEST_REL, manifest)
    write_json(workspace / SESSION_REWRITTEN_ITEMS_REL, final_payload)
    write_text(workspace / SESSION_REWRITTEN_SRT_REL, build_srt(merged_items))

    result["status"] = "completed"
    result["inputs"] = {
        "variables": VARIABLES_REL,
        "final_srt_frame_items": SESSION_FINAL_ITEMS_REL,
        "tts_candidates": SESSION_TTS_CANDIDATES_REL if (workspace / SESSION_TTS_CANDIDATES_REL).exists() else "",
    }
    result["outputs"] = {
        "tool_rewritten_srt_items": OUTPUT_REWRITTEN_ITEMS_REL,
        "rewritten_srt_items": SESSION_REWRITTEN_ITEMS_REL,
        "rewritten_dialogue_srt": SESSION_REWRITTEN_SRT_REL,
        "output_manifest": OUTPUT_MANIFEST_REL,
    }
    result["counts"] = {
        "input_items": len(source_items),
        "rewritten_items": len(merged_items),
        "seconds_per_character": round(float(timing_profile.get("seconds_per_character") or 0.0), 6),
    }
    if timing_profile.get("timing_warning"):
        result["warnings"].append({"code": str(timing_profile["timing_warning"]), "message": "Using fallback seconds-per-character timing."})
    result["created_files"] = [
        WORKING_VARIABLES_REL,
        WORKING_FINAL_ITEMS_REL,
        WORKING_TIMING_PROFILE_REL,
        REWRITE_PROMPT_REL,
        OUTPUT_MODEL_RESPONSE_REL,
        OUTPUT_REWRITTEN_ITEMS_REL,
        OUTPUT_REWRITTEN_SRT_REL,
        OUTPUT_MANIFEST_REL,
        SESSION_REWRITTEN_ITEMS_REL,
        SESSION_REWRITTEN_SRT_REL,
        REPORT_RESULT_REL,
    ]
    return final_payload


def reusable_rewrite_output(existing_payload: Any, variables: dict[str, Any]) -> tuple[bool, str]:
    if not isinstance(existing_payload, dict):
        return False, "existing_rewrite_output_invalid"
    wants_passthrough = strict.wants_original_dialogue_passthrough(variables)
    existing_passthrough = strict.is_original_dialogue_passthrough_payload(existing_payload)
    if wants_passthrough and not existing_passthrough:
        return False, "existing_rewrite_output_not_original_passthrough"
    if not wants_passthrough and existing_passthrough:
        return False, "existing_rewrite_output_is_original_passthrough"
    rewrite_final_prompt, prompt_source = resolve_rewrite_prompt(variables)
    expected_prompt_hash = hashlib.sha256(rewrite_final_prompt.encode("utf-8")).hexdigest()
    existing_prompt_hash = text_value(existing_payload.get("rewrite_final_prompt_sha256"))
    if not wants_passthrough and text_value(existing_payload.get("rewrite_mode")) != "free":
        return False, "existing_rewrite_mode_not_free"
    if existing_prompt_hash != expected_prompt_hash:
        return False, "rewrite_prompt_changed" if existing_prompt_hash else "rewrite_prompt_hash_missing"
    if text_value(existing_payload.get("prompt_source")) != prompt_source:
        return False, "rewrite_prompt_source_changed"
    if dict_value(existing_payload.get("business_context")) != business_context(variables):
        return False, "business_context_changed"
    return True, ""


def scan_for_sensitive_output(payload: dict[str, Any]) -> list[dict[str, str]]:
    text = json.dumps(payload, ensure_ascii=False).lower()
    return [{"code": "sensitive_output_pattern_detected", "message": f"Output contains sensitive-looking pattern: {pattern}"} for pattern in SECRET_PATTERNS if pattern in text]


def run(args: Args) -> dict[str, Any]:
    workspace = resolve_workspace(args.workspace)
    result = base_result(workspace, args)
    try:
        validate_workspace(workspace)
        if args.force:
            force_reset(workspace, result)
        ensure_dirs(workspace)
        for rel in (f"{TOOL_DIR_NAME}/Working", f"{TOOL_DIR_NAME}/Output", PROMPT_DIR_REL, f"{TOOL_DIR_NAME}/Report", "SessionOutput/subtitle"):
            result["prepared_directories"].append(rel)
        variables = load_variables(workspace)
        final_items = load_final_items(workspace)
        write_json(workspace / WORKING_VARIABLES_REL, variables)
        write_json(workspace / WORKING_FINAL_ITEMS_REL, final_items)
        if args.resume and (workspace / SESSION_REWRITTEN_ITEMS_REL).exists() and not args.force:
            final_payload = read_json(workspace / SESSION_REWRITTEN_ITEMS_REL)
            can_reuse, stale_reason = reusable_rewrite_output(final_payload, variables)
            if can_reuse:
                write_json(workspace / OUTPUT_REWRITTEN_ITEMS_REL, final_payload)
                write_text(workspace / OUTPUT_REWRITTEN_SRT_REL, build_srt(final_payload.get("items") or []))
                write_json(workspace / OUTPUT_MANIFEST_REL, build_output_manifest(workspace, [
                    (OUTPUT_REWRITTEN_ITEMS_REL, "subtitle"),
                    (OUTPUT_REWRITTEN_SRT_REL, "subtitle"),
                ]))
                result["status"] = "completed"
                result["outputs"] = {"rewritten_srt_items": SESSION_REWRITTEN_ITEMS_REL, "rewritten_dialogue_srt": SESSION_REWRITTEN_SRT_REL}
                result["counts"] = {"rewritten_items": len(final_payload.get("items") or []), "reused": 1}
                result["warnings"].append({"code": "reused_completed_output", "message": "Existing free rewritten SRT output was reused."})
            else:
                result["warnings"].append({"code": "rewrite_output_stale", "message": f"Existing rewritten SRT output was not reused because {stale_reason}."})
                run_rewrite(workspace, args, variables, final_items, result)
        else:
            run_rewrite(workspace, args, variables, final_items, result)
    except BlockedError as exc:
        add_block(result, exc.code, exc.message)
    except strict.SimplifiedChineseError as exc:
        add_block(result, "simplified_chinese_normalizer_missing", str(exc))
    except PermissionError as exc:
        add_block(result, "workspace_permission_denied", f"Cannot read/write Analysis_V1 workspace. Original error: {exc}")
    except Exception as exc:
        result["status"] = "failed"
        result["warnings"].append({"code": "unexpected_error", "message": str(exc)})
    result["updated_at"] = now_iso()
    result["warnings"].extend(scan_for_sensitive_output(result))
    try:
        if workspace.exists() and workspace.is_dir():
            (workspace / f"{TOOL_DIR_NAME}/Report").mkdir(parents=True, exist_ok=True)
            write_json(workspace / REPORT_RESULT_REL, result)
    except Exception as exc:
        result["warnings"].append({"code": "result_write_failed", "message": str(exc)})
    return result


def parse_args(argv: list[str]) -> Args:
    parser = argparse.ArgumentParser(description="Freely rewrite Analysis_V1 SRT items according to rewrite_prompt.final_prompt.")
    parser.add_argument("--workspace", default="", help="Analysis_V1 workspace. Defaults to current working directory.")
    parser.add_argument("--model-provider", default="", help="Text model provider override. Defaults to SessionContext run_model_provider.")
    parser.add_argument("--model-id", default="", help="Text model id override. Defaults to SessionContext run_model_id.")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--force-regenerate-prompts", action="store_true")
    parser.add_argument("--print-json", action="store_true")
    ns = parser.parse_args(argv)
    return Args(
        workspace=str(ns.workspace or ""),
        model_provider=str(ns.model_provider or ""),
        model_id=str(ns.model_id or ""),
        force=bool(ns.force),
        resume=bool(ns.resume),
        force_regenerate_prompts=bool(ns.force_regenerate_prompts),
        print_json=bool(ns.print_json),
    )


def main(argv: list[str] | None = None) -> int:
    cli_args = argv if argv is not None else sys.argv[1:]
    if "--tool-session-root" in cli_args:
        try:
            from ToolLibrary.Analysis_V1.framework_bridge import maybe_run_framework_bridge
        except ModuleNotFoundError:
            repo_root = str(Path(__file__).resolve().parents[2])
            if repo_root not in sys.path:
                sys.path.insert(0, repo_root)
            from ToolLibrary.Analysis_V1.framework_bridge import maybe_run_framework_bridge

        framework_exit = maybe_run_framework_bridge(cli_args, script_path=Path(__file__), tool_name=TOOL_NAME)
        if framework_exit is not None:
            return framework_exit

    args = parse_args(cli_args)
    result = run(args)
    if args.print_json:
        print(json.dumps(json_safe(result), ensure_ascii=False, indent=2))
    else:
        print(f"{TOOL_NAME} {result['status']}: {result.get('outputs', {}).get('rewritten_srt_items', '')}")
    return 0 if result.get("status") == "completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
