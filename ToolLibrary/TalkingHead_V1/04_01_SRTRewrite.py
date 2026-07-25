from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


TOOL_NAME = "04_01_TalkingHeadSRTRewrite"
TOOL_VERSION = "0.2.0"
TOOL_DIR_NAME = "S6_04_01_SRTRewrite"
VARIABLES_REL = "SessionContext/Variables.json"
SESSION_FINAL_ITEMS_REL = "SessionOutput/subtitle/final_srt_frame_items.json"
SESSION_REWRITTEN_ITEMS_REL = "SessionOutput/subtitle/rewritten_srt_items.json"
SESSION_REWRITTEN_SRT_REL = "SessionOutput/subtitle/rewritten_dialogue.srt"
WORKING_VARIABLES_REL = f"{TOOL_DIR_NAME}/Working/InputFrom_0_Variables.json"
WORKING_FINAL_ITEMS_REL = f"{TOOL_DIR_NAME}/Working/InputFrom_4_final_srt_frame_items.json"
PROMPT_DIR_REL = f"{TOOL_DIR_NAME}/Prompt"
PROMPT_GENERATE_REL = f"{PROMPT_DIR_REL}/00_talking_head_srt_generate_prompt.md"
OUTPUT_MODEL_RESPONSE_REL = f"{TOOL_DIR_NAME}/Output/model_response.json"
OUTPUT_REWRITTEN_ITEMS_REL = f"{TOOL_DIR_NAME}/Output/rewritten_srt_items.json"
REPORT_RESULT_REL = f"{TOOL_DIR_NAME}/Report/Result.json"
ANALYSIS_REWRITE_PATH = Path(__file__).resolve().parents[1] / "Analysis_V1" / "04_01_SRTRewrite.py"


class BlockedError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Args:
    workspace: str
    model_provider: str
    model_id: str
    database_url: str
    database_url_env: str
    force: bool
    resume: bool
    print_json: bool


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def text_value(value: Any) -> str:
    return str(value or "").strip()


def dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def list_value(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def resolve_workspace(raw_workspace: str) -> Path:
    workspace = Path(raw_workspace).expanduser() if raw_workspace else Path.cwd()
    return workspace.resolve()


def ensure_dirs(workspace: Path) -> None:
    for rel in (
        f"{TOOL_DIR_NAME}/Working",
        f"{TOOL_DIR_NAME}/Output",
        PROMPT_DIR_REL,
        f"{TOOL_DIR_NAME}/Report",
        "SessionOutput/subtitle",
    ):
        (workspace / rel).mkdir(parents=True, exist_ok=True)


def remove_path(path: Path) -> None:
    if not path.exists():
        return
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def force_reset(workspace: Path) -> list[dict[str, str]]:
    actions: list[dict[str, str]] = []
    for rel in (TOOL_DIR_NAME, SESSION_REWRITTEN_ITEMS_REL, SESSION_REWRITTEN_SRT_REL):
        path = workspace / rel
        if path.exists():
            remove_path(path)
            actions.append({"path": rel, "action": "removed_for_force_rerun"})
    return actions


def srt_time(seconds: float) -> str:
    total_ms = max(0, int(round(seconds * 1000)))
    ms = total_ms % 1000
    total_s = total_ms // 1000
    s = total_s % 60
    total_m = total_s // 60
    m = total_m % 60
    h = total_m // 60
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def build_srt(items: list[dict[str, Any]]) -> str:
    blocks: list[str] = []
    for index, item in enumerate(items, 1):
        start = float(item.get("start") or 0)
        end = float(item.get("end") or start + float(item.get("duration") or 0) or start + 1)
        text = text_value(item.get("dialogue") or item.get("text"))
        blocks.append(f"{index}\n{srt_time(start)} --> {srt_time(end)}\n{text}")
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def split_plain_script(script: str) -> list[str]:
    lines: list[str] = []
    for raw in str(script or "").splitlines():
        text = re.sub(r"^\s*(?:\d+[\.\、\)]|[-*])\s*", "", raw).strip()
        if text:
            lines.append(text)
    if lines:
        return lines
    parts = [part.strip() for part in re.split(r"(?<=[。！？!?])\s*", str(script or "").strip()) if part.strip()]
    return parts


def compact_task_summary(value: Any, limit: int = 80) -> str:
    summary = re.sub(r"\s+", " ", text_value(value)).strip(" ，,。；;：:")
    if not summary:
        return ""
    return summary if len(summary) <= limit else summary[: max(1, limit - 1)].rstrip(" ，,。；;：:") + "…"


def task_summary_from_script(
    items: list[dict[str, Any]],
    variables: dict[str, Any],
    suggested_summary: str = "",
) -> str:
    suggested = compact_task_summary(suggested_summary)
    if suggested:
        return suggested
    business = dict_value(variables.get("business_context"))
    industry = text_value(business.get("industry"))
    audience = text_value(business.get("target_audience"))
    product = text_value(business.get("product_info"))
    if product in {"-", "无", "没有", "None", "none"}:
        product = ""
    script_excerpt = compact_task_summary(
        "".join(text_value(item.get("dialogue") or item.get("text")) for item in items),
        56,
    )
    if product:
        prefix = f"围绕{product}的人物口播"
    elif industry and audience:
        prefix = f"面向{audience}的{industry}人物口播"
    elif industry:
        prefix = f"{industry}人物口播"
    else:
        prefix = "人物口播"
    return compact_task_summary(f"{prefix}：{script_excerpt}" if script_excerpt else prefix)


def segment_target_seconds(variables: dict[str, Any]) -> float:
    talking_head = dict_value(variables.get("talking_head"))
    segment_planning = dict_value(talking_head.get("segment_planning"))
    quick_config = dict_value(variables.get("storyboard_quick_config"))
    try:
        return max(1.0, float(segment_planning.get("srt_target_seconds") or quick_config.get("target_shot_seconds") or variables.get("srt_target_seconds") or 8.0))
    except Exception:
        return 8.0


def normalize_item(item: dict[str, Any], index: int, target_seconds: float, current_start: float | None = None) -> dict[str, Any]:
    start = item.get("start")
    end = item.get("end")
    duration = item.get("duration")
    try:
        start_f = float(start)
    except Exception:
        start_f = float(current_start or 0)
    try:
        duration_f = float(duration)
    except Exception:
        duration_f = 0.0
    try:
        end_f = float(end)
    except Exception:
        end_f = start_f + (duration_f if duration_f > 0 else target_seconds)
    if end_f <= start_f:
        end_f = start_f + (duration_f if duration_f > 0 else target_seconds)
    duration_f = max(0.001, end_f - start_f)
    return {
        **item,
        "srt_id": text_value(item.get("srt_id") or item.get("id") or f"srt_{index:04d}"),
        "index": int(item.get("index") or index),
        "start": round(start_f, 3),
        "end": round(end_f, 3),
        "duration": round(duration_f, 3),
        "dialogue": text_value(item.get("dialogue") or item.get("text")),
    }


def items_from_script(script: str, target_seconds: float) -> list[dict[str, Any]]:
    current = 0.0
    items: list[dict[str, Any]] = []
    for index, dialogue in enumerate(split_plain_script(script), 1):
        item = normalize_item({"dialogue": dialogue}, index, target_seconds, current)
        items.append(item)
        current = float(item["end"])
    return items


def script_creation(variables: dict[str, Any]) -> dict[str, Any]:
    workflow = dict_value(variables.get("workflow"))
    return dict_value(dict_value(workflow.get("script_creation")) or variables.get("script_creation"))


def read_declared_script(workspace: Path, variables: dict[str, Any], kind: str) -> tuple[str, str]:
    creation = script_creation(variables)
    input_config = dict_value(creation.get("input"))
    descriptor = dict_value(input_config.get(kind))
    rel_path = text_value(descriptor.get("path"))
    if not rel_path:
        return "", ""
    path = workspace / rel_path
    if not path.is_file():
        raise BlockedError("talking_head_declared_script_missing", f"Variables 声明的脚本文件不存在：{rel_path}")
    return path.read_text(encoding="utf-8", errors="ignore").strip(), rel_path


def prompt_candidates(variables: dict[str, Any]) -> list[tuple[str, str, str]]:
    creation = script_creation(variables)
    rows = [
        ("complex", text_value(creation.get("final_prompt")), "Variables.workflow.script_creation.final_prompt"),
        ("simple", text_value(creation.get("simple_prompt")), "Variables.workflow.script_creation.simple_prompt"),
    ]
    return [(kind, prompt, source) for kind, prompt, source in rows if prompt]


def load_analysis_rewrite_module() -> Any:
    spec = importlib.util.spec_from_file_location("analysis_v1_04_01_srt_rewrite", ANALYSIS_REWRITE_PATH)
    if spec is None or spec.loader is None:
        raise BlockedError("analysis_rewrite_module_missing", f"Cannot load Analysis_V1 rewrite module: {ANALYSIS_REWRITE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("analysis_v1_04_01_srt_rewrite", module)
    spec.loader.exec_module(module)
    return module


def build_prompt_generation_prompt(prompt: str, prompt_kind: str, prompt_source: str, target_seconds: float, mode: str, reference_script: str = "") -> str:
    mode_instruction = (
        "请从零创作完整人物口播脚本，不依赖参考对白。"
        if mode == "ai_create"
        else "请逐句改写下方参考脚本，保持输入句数和顺序完全一致，不合并、不拆分、不新增、不删除。"
    )
    reference_block = f"\n参考脚本：\n{reference_script}\n" if reference_script else ""
    return f"""请根据下面的{prompt_kind}提示词，为人物口播任务生成 SRT 台词分句。

要求：
 - {mode_instruction}
- 输出必须是 JSON 对象，不要 Markdown。
- JSON 格式：{{"task_summary":"40-80 个中文字符的一句话任务简介","items":[{{"srt_id":"srt_0001","dialogue":"第一句台词"}}, ...]}}
- task_summary 必须概括最终生成脚本讲什么，优先体现产品、主题、目标受众和核心表达方向。
- 每个 item 只保留一个完整句子或自然口播短句，不要拆半句话。
- 不要生成空镜描述，不要生成镜头说明，只生成主持人口播台词。
- 每句目标视频长度约 {target_seconds:g} 秒；如果一句较长，保持语义完整，不要硬切断。
- 至少生成 1 条，最多生成 80 条。

提示词来源：{prompt_source}

提示词正文：
{prompt}
{reference_block}
"""


def model_generate_items(workspace: Path, args: Args, variables: dict[str, Any], mode: str) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    candidates = prompt_candidates(variables)
    if not candidates:
        raise BlockedError("talking_head_script_or_prompt_missing", "人物口播 04_01 需要脚本、复杂提示词或简单提示词；当前三者都为空。")
    prompt_kind, prompt, prompt_source = candidates[0]
    target_seconds = segment_target_seconds(variables)
    reference_script, reference_path = read_declared_script(workspace, variables, "reference_script") if mode == "ai_rewrite" else ("", "")
    if mode == "ai_rewrite" and not reference_script:
        raise BlockedError("talking_head_reference_script_missing", "智能改写脚本模式缺少 Variables 声明的参考脚本。")
    prompt_path = workspace / PROMPT_GENERATE_REL
    write_text(prompt_path, build_prompt_generation_prompt(prompt, prompt_kind, prompt_source, target_seconds, mode, reference_script))
    analysis = load_analysis_rewrite_module()
    analysis_args = analysis.Args(
        workspace=str(workspace),
        model_provider=args.model_provider,
        model_id=args.model_id,
        database_url=args.database_url,
        database_url_env=args.database_url_env,
        force=args.force,
        resume=args.resume,
        force_regenerate_prompts=False,
        max_repair_attempts=0,
        print_json=args.print_json,
    )
    config = analysis.resolve_model_config(analysis_args, variables)
    response = analysis.call_text_model_with_variables(analysis_args, variables, config, prompt_path)
    raw_items = [item for item in list_value(response.get("items")) if isinstance(item, dict)]
    if not raw_items:
        raise BlockedError("talking_head_prompt_model_output_empty", "提示词生成 SRT 结果为空。")
    current = 0.0
    items = []
    for index, item in enumerate(raw_items, 1):
        normalized = normalize_item(item, index, target_seconds, current)
        if normalized["dialogue"]:
            items.append(normalized)
            current = float(normalized["end"])
    if not items:
        raise BlockedError("talking_head_prompt_model_dialogue_empty", "提示词生成结果没有有效口播台词。")
    return items, reference_path or prompt_source, {"provider": config.provider, "model": config.model, "source": config.source, "prompt_source": prompt_source, "response": response}


def build_payload(
    items: list[dict[str, Any]],
    source: str,
    variables: dict[str, Any],
    model: dict[str, Any] | None = None,
    task_summary: str = "",
) -> dict[str, Any]:
    prompt_hash = hashlib.sha256(source.encode("utf-8")).hexdigest() if source else ""
    return {
        "schema_version": "analysis_v1_rewritten_srt_items_0.1",
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "workflow_id": "person_talking_head_v1",
        "source_items_path": source,
        "prompt_source": source,
        "rewrite_final_prompt_sha256": prompt_hash,
        "model": model or {"provider": "", "model": "", "source": "script_passthrough"},
        "passthrough_original_dialogue": model is None,
        "source_item_count": len(items),
        "task_summary": task_summary_from_script(items, variables, task_summary),
        "items": items,
        "created_at": now_iso(),
        "talking_head": dict_value(variables.get("talking_head")),
    }


def base_result(workspace: Path, args: Args) -> dict[str, Any]:
    return {
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "workflow_id": "person_talking_head_v1",
        "status": "completed",
        "workspace_dir": str(workspace),
        "requires_model_calls": False,
        "inputs": {},
        "outputs": {},
        "counts": {},
        "created_files": [],
        "cleanup_actions": [],
        "warnings": [],
        "blocked_reasons": [],
        "force": bool(args.force),
        "resume": bool(args.resume),
        "updated_at": now_iso(),
    }


def run(args: Args) -> dict[str, Any]:
    workspace = resolve_workspace(args.workspace)
    result = base_result(workspace, args)
    try:
        if not workspace.is_dir():
            raise BlockedError("workspace_missing", f"Workspace does not exist: {workspace}")
        if args.force:
            result["cleanup_actions"].extend(force_reset(workspace))
        ensure_dirs(workspace)
        variables = read_json(workspace / VARIABLES_REL, {}) or {}
        if not isinstance(variables, dict):
            variables = {}
        if text_value(variables.get("workflow_id")) != "person_talking_head_v1":
            raise BlockedError("talking_head_variables_missing", "请先运行 TalkingHead_V1/00 生成当前工作流 Variables。")
        mode = text_value(script_creation(variables).get("mode") or variables.get("script_creation_mode"))
        if mode not in {"user_provided", "ai_create", "ai_rewrite"}:
            raise BlockedError("talking_head_script_creation_mode_invalid", f"Variables 中的脚本模式无效：{mode!r}")
        items: list[dict[str, Any]] = []
        source = ""
        model_meta: dict[str, Any] | None = None
        if mode == "user_provided":
            user_script, source = read_declared_script(workspace, variables, "user_script")
            if not user_script:
                raise BlockedError("talking_head_user_script_missing", "用户给定脚本模式缺少 Variables 声明的完整脚本。")
            items = items_from_script(user_script, segment_target_seconds(variables))
            task_summary = task_summary_from_script(items, variables)
            payload = build_payload(items, source, variables, task_summary=task_summary)
            result["warnings"].append({
                "code": "talking_head_script_passthrough",
                "message": "人物口播已提供脚本/SRT，04_01 直接使用脚本台词，不要求 rewrite_final_prompt。",
            })
        else:
            items, source, model_meta = model_generate_items(workspace, args, variables, mode)
            model_response = model_meta.pop("response", {})
            write_json(workspace / OUTPUT_MODEL_RESPONSE_REL, model_response)
            task_summary = task_summary_from_script(items, variables, text_value(model_response.get("task_summary")))
            payload = build_payload(items, source, variables, model_meta, task_summary=task_summary)
            result["requires_model_calls"] = True
        variables["task_summary"] = text_value(payload.get("task_summary"))
        variables["updated_at"] = now_iso()
        write_json(workspace / VARIABLES_REL, variables)
        source_items = items
        if mode == "ai_rewrite":
            reference_script, _ = read_declared_script(workspace, variables, "reference_script")
            source_items = items_from_script(reference_script, segment_target_seconds(variables))
        final_items_payload = {
            "schema_version": "analysis_v1_final_srt_frame_items_0.1",
            "source_type": "person_talking_head_reference_script" if mode == "ai_rewrite" else ("person_talking_head_prompt" if model_meta else "person_talking_head_script"),
            "items": source_items,
        }
        write_json(workspace / WORKING_VARIABLES_REL, variables)
        write_json(workspace / WORKING_FINAL_ITEMS_REL, final_items_payload)
        write_json(workspace / OUTPUT_REWRITTEN_ITEMS_REL, payload)
        write_json(workspace / SESSION_FINAL_ITEMS_REL, final_items_payload)
        write_json(workspace / SESSION_REWRITTEN_ITEMS_REL, payload)
        write_text(workspace / SESSION_REWRITTEN_SRT_REL, build_srt(items))
        result["inputs"] = {
            "variables": VARIABLES_REL if (workspace / VARIABLES_REL).exists() else "",
            "source": source,
            "script_creation_mode": mode,
        }
        result["outputs"] = {
            "final_srt_frame_items": SESSION_FINAL_ITEMS_REL,
            "rewritten_srt_items": SESSION_REWRITTEN_ITEMS_REL,
            "rewritten_dialogue_srt": SESSION_REWRITTEN_SRT_REL,
            "task_summary": variables["task_summary"],
        }
        result["counts"] = {"items": len(items), "model_generated": 1 if model_meta else 0}
        result["created_files"] = [
            WORKING_VARIABLES_REL,
            WORKING_FINAL_ITEMS_REL,
            OUTPUT_REWRITTEN_ITEMS_REL,
            SESSION_FINAL_ITEMS_REL,
            SESSION_REWRITTEN_ITEMS_REL,
            SESSION_REWRITTEN_SRT_REL,
            REPORT_RESULT_REL,
        ]
        if model_meta:
            result["created_files"].insert(2, PROMPT_GENERATE_REL)
            result["created_files"].insert(3, OUTPUT_MODEL_RESPONSE_REL)
    except BlockedError as exc:
        result["status"] = "blocked"
        result["blocked_reasons"].append({"code": exc.code, "message": exc.message})
    except Exception as exc:
        code = text_value(getattr(exc, "code", ""))
        message = text_value(getattr(exc, "message", "")) or str(exc)
        if code:
            result["status"] = "blocked"
            result["blocked_reasons"].append({"code": code, "message": message})
        else:
            result["status"] = "failed"
            result["warnings"].append({"code": "unexpected_error", "message": message})
    result["updated_at"] = now_iso()
    try:
        write_json(workspace / REPORT_RESULT_REL, result)
    except Exception:
        pass
    return result


def parse_args(argv: list[str]) -> Args:
    parser = argparse.ArgumentParser(description="TalkingHead_V1 SRT rewrite or prompt-to-SRT generation.")
    parser.add_argument("--workspace", default="")
    parser.add_argument("--model-provider", default="")
    parser.add_argument("--model-id", default="")
    parser.add_argument("--database-url", default="")
    parser.add_argument("--database-url-env", default="OPENCREW_DATABASE_URL")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--print-json", action="store_true")
    ns = parser.parse_args(argv)
    return Args(
        workspace=str(ns.workspace or ""),
        model_provider=str(ns.model_provider or ""),
        model_id=str(ns.model_id or ""),
        database_url=str(ns.database_url or ""),
        database_url_env=str(ns.database_url_env or "OPENCREW_DATABASE_URL"),
        force=bool(ns.force),
        resume=bool(ns.resume),
        print_json=bool(ns.print_json),
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    result = run(args)
    if args.print_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"{TOOL_NAME} {result['status']}: {result.get('outputs', {}).get('rewritten_srt_items', '')}")
    return 0 if result["status"] in {"completed", "blocked"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
