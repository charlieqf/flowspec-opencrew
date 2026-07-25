from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from OpenCrew.ToolLibrary.Analysis_V1 import DEFAULT_DATABASE_URL_ENV, DEFAULT_OPENCREW_DATABASE_URL
except Exception:  # pragma: no cover
    DEFAULT_DATABASE_URL_ENV = "OPENCREW_DATABASE_URL"
    DEFAULT_OPENCREW_DATABASE_URL = "postgresql+psycopg://opencrew:opencrew@127.0.0.1:5433/opencrew"

TOOLLIB_ROOT = Path(__file__).resolve().parents[1]
if str(TOOLLIB_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLLIB_ROOT))
try:
    from opencode_autoheal import is_opencode_session_not_found, recover_opencode_session_id
except Exception:  # pragma: no cover - standalone legacy fallback
    def is_opencode_session_not_found(exc: BaseException) -> bool:
        return False

    def recover_opencode_session_id(**_: Any) -> str:
        raise RuntimeError("opencode_autoheal is unavailable")

try:
    from Analysis_V1.simplified_chinese import SimplifiedChineseError, contains_traditional_chinese, to_simplified_chinese
except Exception:  # pragma: no cover - direct script execution fallback
    from simplified_chinese import SimplifiedChineseError, contains_traditional_chinese, to_simplified_chinese  # type: ignore


TOOL_NAME = "04_01_SRTRewrite"
TOOL_VERSION = "0.1.0"
CONTEXT_DIR_NAME = "SessionContext"
VARIABLES_REL = f"{CONTEXT_DIR_NAME}/Variables.json"
TOOL_DIR_NAME = "S6_04_01_SRTRewrite"
WORKING_VARIABLES_REL = f"{TOOL_DIR_NAME}/Working/InputFrom_0_Variables.json"
WORKING_FINAL_ITEMS_REL = f"{TOOL_DIR_NAME}/Working/InputFrom_4_final_srt_frame_items.json"
PROMPT_DIR_REL = f"{TOOL_DIR_NAME}/Prompt"
REWRITE_PROMPT_REL = f"{PROMPT_DIR_REL}/00_srt_rewrite_prompt.md"
REPAIR_PROMPT_REL = f"{PROMPT_DIR_REL}/01_srt_rewrite_repair_prompt.md"
OUTPUT_MODEL_RESPONSE_REL = f"{TOOL_DIR_NAME}/Output/model_response.json"
OUTPUT_REWRITTEN_ITEMS_REL = f"{TOOL_DIR_NAME}/Output/rewritten_srt_items.json"
REPORT_RESULT_REL = f"{TOOL_DIR_NAME}/Report/Result.json"
SESSION_FINAL_ITEMS_REL = "SessionOutput/subtitle/final_srt_frame_items.json"
SESSION_REWRITTEN_ITEMS_REL = "SessionOutput/subtitle/rewritten_srt_items.json"
SESSION_REWRITTEN_SRT_REL = "SessionOutput/subtitle/rewritten_dialogue.srt"
ORIGINAL_DIALOGUE_PASSTHROUGH_POLICY = "original_dialogue_passthrough"
SECRET_PATTERNS = (
    "postgresql://",
    "postgresql+psycopg://",
    "password",
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "authorization",
    "bearer ",
    "cookie",
)


class BlockedError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class ToolError(RuntimeError):
    pass


class DatabaseError(RuntimeError):
    pass


@dataclass(frozen=True)
class Args:
    workspace: str
    model_provider: str
    model_id: str
    database_url: str
    database_url_env: str
    force: bool
    resume: bool
    force_regenerate_prompts: bool
    max_repair_attempts: int
    print_json: bool


@dataclass(frozen=True)
class ModelConfig:
    provider: str
    model: str
    source: str


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if hasattr(value, "tolist"):
        return json_safe(value.tolist())
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            return str(value)
    return value


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
    candidate = Path(path)
    try:
        return candidate.resolve().relative_to(workspace.resolve()).as_posix()
    except Exception:
        return str(path)


def resolve_workspace(raw_workspace: str) -> Path:
    workspace = Path(raw_workspace).expanduser() if raw_workspace else Path.cwd()
    try:
        return workspace.resolve()
    except Exception:
        return workspace.absolute()


def validate_workspace(workspace: Path) -> None:
    if not workspace.exists():
        raise BlockedError("workspace_missing", f"Workspace does not exist: {workspace}")
    if not workspace.is_dir():
        raise BlockedError("workspace_not_directory", f"Workspace is not a directory: {workspace}")


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
        "requires_database": True,
        "requires_model_calls": True,
        "model_call_policy": {
            "prompt_policy": "all_model_prompts_must_be_written_to_prompt_dir_and_read_from_files",
            "hidden_prompt_concatenation": "forbidden",
            "repair_calls": int(args.max_repair_attempts),
        },
        "inputs": {},
        "outputs": {},
        "counts": {},
        "created_files": [],
        "prepared_directories": [],
        "cleanup_actions": [],
        "warnings": [],
        "blocked_reasons": [],
        "force": bool(args.force),
        "resume": bool(args.resume),
        "updated_at": now_iso(),
    }


def add_block(result: dict[str, Any], code: str, message: str) -> None:
    result["status"] = "blocked"
    result.setdefault("blocked_reasons", []).append({"code": code, "message": message})


def force_reset(workspace: Path, result: dict[str, Any]) -> None:
    for rel in (TOOL_DIR_NAME, SESSION_REWRITTEN_ITEMS_REL, SESSION_REWRITTEN_SRT_REL):
        path = workspace / rel
        if path.exists():
            remove_path(path)
            result.setdefault("cleanup_actions", []).append({"path": rel, "action": "removed_for_force_rerun"})


def text_value(value: Any) -> str:
    return str(value or "").strip()


def dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def business_context(variables: dict[str, Any]) -> dict[str, str]:
    explicit = dict_value(variables.get("business_context"))
    keys = ("industry", "persona", "target_audience", "product_info", "constraints", "video_formula")
    return {key: text_value(explicit.get(key) or variables.get(key)) for key in keys}


def compact_intent_text(value: Any) -> str:
    text = str(value or "").lower()
    return re.sub(r"[\s\u3000，。！？、；：,.!?;:\"'“”‘’（）()\[\]【】《》<>…—\-_/\\|·~`]+", "", text)


def compact_intent_clauses(values: list[Any]) -> list[str]:
    clauses: list[str] = []
    for value in values:
        for part in re.split(r"[\n\r。！？；;!?]+", str(value or "")):
            compact = compact_intent_text(part)
            if compact:
                clauses.append(compact)
    return clauses


def wants_original_dialogue_passthrough(variables: dict[str, Any]) -> bool:
    rewrite = dict_value(variables.get("rewrite_prompt"))
    raw_parts = [
        rewrite.get("simple_prompt"),
        rewrite.get("final_prompt"),
        variables.get("rewrite_simple_prompt"),
        variables.get("rewrite_final_prompt"),
        variables.get("simple_prompt"),
        variables.get("final_prompt"),
        variables.get("product_info"),
        variables.get("constraints"),
    ]
    explicit = dict_value(variables.get("business_context"))
    raw_parts.extend([explicit.get("product_info"), explicit.get("constraints")])
    text = compact_intent_text("\n".join(str(part or "") for part in raw_parts))
    if not text:
        return False
    clauses = compact_intent_clauses(raw_parts)
    explicit_phrases = (
        "改写srt要和原srt一样",
        "改写srt和原srt一样",
        "改写srt与原srt一样",
        "改写srt保持原srt",
        "改写srt保留原srt",
        "rewrittensrt要和原srt一样",
        "rewrittensrt和原srt一样",
        "rewrittensrt与原srt一样",
        "还原我的原视频脚本",
        "还原原视频脚本",
        "还原我的原视频口播脚本",
        "还原原视频口播脚本",
        "还原我的原视频口播文案",
        "还原原视频口播文案",
        "需要我原视频的口播脚本",
        "使用原视频口播脚本",
        "使用原视频脚本",
        "直接用原视频脚本",
        "直接使用原视频脚本",
        "不要改写口播",
        "不改写口播",
        "不要改写对白",
        "不改写对白",
        "严格按照原脚本进行",
        "严格按原脚本进行",
        "按照原脚本进行",
        "按原脚本进行",
        "不要改原脚本",
        "不改原脚本",
        "不要修改原脚本",
        "不修改原脚本",
        "按照原脚本一句一句生成",
        "按原脚本一句一句生成",
        "严格按照我的脚本进行",
        "严格按我的脚本进行",
        "按照我的脚本进行不要修改我的脚本",
        "按我的脚本进行不要修改我的脚本",
        "按照我的脚本不要修改我的脚本",
        "按我的脚本不要修改我的脚本",
        "按照我的脚本进行不要改写我的脚本",
        "按我的脚本进行不要改写我的脚本",
        "按照我的脚本不要改写我的脚本",
        "按我的脚本不要改写我的脚本",
        "不要改写我的脚本",
        "不改写我的脚本",
        "不要改我的脚本",
        "不改我的脚本",
        "不要修改我的脚本",
        "不修改我的脚本",
        "不要动我的脚本",
        "别动我的脚本",
        "我的脚本不要改",
        "我的脚本不要改写",
        "我的脚本别改",
        "我的脚本别动",
        "我给的脚本不要改",
        "我给的脚本不要改写",
        "我给的脚本别改",
        "脚本不要改",
        "脚本不要改写",
        "脚本别改",
        "脚本别动",
        "文案不要改",
        "文案别改",
        "台词不要改",
        "台词别改",
        "口播不要改",
        "口播别改",
        "对白不要改",
        "对白别改",
        "脚本一个字都不要改",
        "脚本一个字也不要改",
        "脚本一字不改",
        "文案一个字都不要改",
        "文案一个字也不要改",
        "文案一字不改",
        "台词一个字都不要改",
        "台词一个字也不要改",
        "台词一字不改",
        "口播一个字都不要改",
        "口播一个字也不要改",
        "口播一字不改",
        "对白一个字都不要改",
        "对白一个字也不要改",
        "对白一字不改",
    )
    if any(phrase in text for phrase in explicit_phrases):
        return True
    source_terms = (
        "原srt",
        "原始srt",
        "原字幕",
        "原始字幕",
        "原视频脚本",
        "原视频口播",
        "原视频文案",
        "原口播脚本",
        "原口播文案",
        "原对白",
        "原脚本",
        "原文案",
        "原始对白",
        "我的srt",
        "当前srt",
        "输入srt",
        "给定srt",
        "我的脚本",
        "我给的脚本",
        "给的脚本",
        "给定脚本",
        "用户脚本",
        "用户给的脚本",
        "这个脚本",
        "这段脚本",
        "这份脚本",
        "当前脚本",
        "现有脚本",
        "脚本内容",
        "脚本",
        "我的文案",
        "我给的文案",
        "给的文案",
        "给定文案",
        "这个文案",
        "当前文案",
        "文案内容",
        "文案",
        "我的台词",
        "我给的台词",
        "给的台词",
        "给定台词",
        "台词内容",
        "台词",
        "我的口播",
        "我给的口播",
        "给的口播",
        "给定口播",
        "口播内容",
        "口播",
        "我的对白",
        "我给的对白",
        "给的对白",
        "给定对白",
        "对白内容",
        "对白",
        "originalsrt",
        "originalscript",
        "originaldialogue",
        "originaltranscript",
    )
    preserve_terms = (
        "不要改写",
        "禁止改写",
        "不能改写",
        "别改写",
        "不改写",
        "不要修改",
        "禁止修改",
        "不能修改",
        "别修改",
        "不修改",
        "不要改",
        "禁止改",
        "不能改",
        "别改",
        "不改",
        "不要动",
        "别动",
        "不动",
        "不要改动",
        "别改动",
        "不改动",
        "不要改词",
        "别改词",
        "不改词",
        "不要换词",
        "别换词",
        "不换词",
        "不需要改写",
        "不用改写",
        "无需改写",
        "不重新改写",
        "不要重新改写",
        "保持原样",
        "保留原样",
        "保留原文",
        "直接复制",
        "直接使用",
        "直接用",
        "照抄",
        "还原",
        "恢复",
        "一样",
        "一模一样",
        "一字不改",
        "一个字不改",
        "一个字都不要改",
        "一个字也不要改",
        "keeporiginal",
        "preserveoriginal",
        "donotrewrite",
        "norewrite",
        "copyoriginal",
    )
    metadata_terms = (
        "srtid",
        "srt_id",
        "编号",
        "时间",
        "时间轴",
        "图片帧",
        "图片",
        "帧",
        "句数",
        "顺序",
        "结构",
        "格式",
        "start",
        "end",
        "duration",
        "imagepath",
    )
    content_terms = (
        "内容",
        "文本",
        "文字",
        "文案",
        "台词",
        "对白",
        "口播",
        "原文",
        "原句",
        "词",
        "字",
    )
    positive_rewrite_terms = (
        "改写",
        "重写",
        "替换",
        "生成新对白",
        "生成新的口播对白",
        "生成新的对白",
        "生成新句子",
        "对应新句子",
        "新对白",
        "新句子",
    )
    rewrite_negation_terms = (
        "不要改写",
        "禁止改写",
        "不能改写",
        "别改写",
        "不改写",
        "不需要改写",
        "不用改写",
        "无需改写",
        "不重新改写",
        "不要重新改写",
        "不要重写",
        "禁止重写",
        "不能重写",
        "别重写",
        "不重写",
        "不要替换",
        "禁止替换",
        "不能替换",
        "别替换",
        "不替换",
    )
    metadata_preserve_terms = (
        "保持原样",
        "保留原样",
        "不变",
        "保持不变",
        "不改",
        "不要改",
        "不改变",
        "不要改变",
        "不更改",
        "不调整",
        "不得改变",
        "不得调整",
        "一字不改",
        "一个字都不要改",
        "一个字也不要改",
    )
    content_preserve_phrases = (
        "对白保持原样",
        "对白内容保持原样",
        "口播保持原样",
        "口播内容保持原样",
        "台词保持原样",
        "台词内容保持原样",
        "文案保持原样",
        "文案内容保持原样",
        "脚本保持原样",
        "脚本内容保持原样",
        "原对白保持原样",
        "原口播保持原样",
        "原台词保持原样",
        "原文案保持原样",
        "原脚本保持原样",
        "对白一字不改",
        "口播一字不改",
        "台词一字不改",
        "文案一字不改",
        "脚本一字不改",
    )
    for clause in clauses:
        if not any(term in clause for term in source_terms) or not any(term in clause for term in preserve_terms):
            continue
        if (
            any(term in clause for term in positive_rewrite_terms)
            and not any(term in clause for term in rewrite_negation_terms)
            and any(term in clause for term in metadata_terms)
            and any(term in clause for term in metadata_preserve_terms)
            and not any(term in clause for term in content_preserve_phrases)
        ):
            continue
        if any(term in clause for term in metadata_terms) and not any(term in clause for term in content_terms):
            continue
        return True
    return False


def resolve_rewrite_prompt(variables: dict[str, Any]) -> tuple[str, str]:
    rewrite = dict_value(variables.get("rewrite_prompt"))
    prompt = (
        text_value(rewrite.get("final_prompt"))
        or text_value(variables.get("rewrite_final_prompt"))
        or text_value(variables.get("final_prompt"))
    )
    source = (
        text_value(rewrite.get("source"))
        or ("SessionContext/Variables.json:rewrite_prompt.final_prompt" if text_value(rewrite.get("final_prompt")) else "")
        or ("SessionContext/Variables.json:rewrite_final_prompt" if text_value(variables.get("rewrite_final_prompt")) else "")
        or ("SessionContext/Variables.json:final_prompt" if text_value(variables.get("final_prompt")) else "")
    )
    if not prompt:
        raise BlockedError("rewrite_final_prompt_missing", "04_01_SRTRewrite requires rewrite_prompt.final_prompt. The legacy final_prompt fallback is also empty.")
    return prompt, source


def resolve_model_config(args: Args, variables: dict[str, Any]) -> ModelConfig:
    rewrite_model = dict_value(variables.get("rewrite_model_config"))
    provider = (
        text_value(args.model_provider)
        or text_value(rewrite_model.get("provider"))
        or text_value(variables.get("run_model_provider"))
    )
    model = (
        text_value(args.model_id)
        or text_value(rewrite_model.get("model"))
        or text_value(variables.get("run_model_id"))
    )
    if not provider or not model:
        raise BlockedError(
            "text_model_config_missing",
            "04_01_SRTRewrite requires a run model. Provide --model-provider/--model-id or set run_model_provider/run_model_id in SessionContext/Variables.json.",
        )
    return ModelConfig(provider=provider, model=model, source="cli" if args.model_provider or args.model_id else "SessionContext/Variables.json:run_model")


def normalize_database_url(value: str) -> str:
    return value.replace("postgresql+psycopg://", "postgresql://", 1).replace("postgresql+psycopg2://", "postgresql://", 1)


def resolve_database_url(args: Args) -> str:
    return text_value(args.database_url) or os.environ.get(args.database_url_env or DEFAULT_DATABASE_URL_ENV, "") or DEFAULT_OPENCREW_DATABASE_URL


def decode_db_value(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace").strip()
    if isinstance(value, memoryview):
        return value.tobytes().decode("utf-8", errors="replace").strip()
    return str(value or "").strip()


def classify_database_exception(exc: Exception) -> str:
    text = str(exc).lower()
    if "password authentication failed" in text or "authentication failed" in text or "fe_sendauth" in text:
        return "database_auth_failed"
    if "connection refused" in text:
        return "database_connection_refused"
    if "operation not permitted" in text or "eperm" in text or "permission denied" in text or "network is unreachable" in text:
        return "database_network_blocked"
    return "database_query_failed"


def postgres_connect(database_url: str) -> Any:
    normalized_url = normalize_database_url(database_url)
    try:
        import psycopg  # type: ignore

        conn = psycopg.connect(normalized_url, connect_timeout=8)
        conn.execute("SET client_encoding TO 'UTF8'")
        return conn
    except ImportError:
        try:
            import psycopg2  # type: ignore
        except ImportError as exc:
            raise DatabaseError("database_driver_missing") from exc
        try:
            conn = psycopg2.connect(normalized_url, connect_timeout=8)
            conn.set_client_encoding("UTF8")
            return conn
        except Exception as exc:
            raise DatabaseError(classify_database_exception(exc)) from exc
    except Exception as exc:
        raise DatabaseError(classify_database_exception(exc)) from exc


def fetch_opencode_runtime(args: Args) -> dict[str, str]:
    sql = """
SELECT base_url, auth_username, auth_password
FROM opencode_runtime
WHERE id = 1
LIMIT 1
"""
    try:
        conn = postgres_connect(resolve_database_url(args))
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
                row = cur.fetchone()
        finally:
            conn.close()
    except DatabaseError as exc:
        raise BlockedError(str(exc) or "database_query_failed", f"Cannot read OpenCode runtime from database: {exc.__cause__ or exc}") from exc
    if not row:
        raise BlockedError("opencode_runtime_missing", "OpenCode runtime is missing. Please reconnect OpenCode in OpenCrew Step 1.")
    base_url = decode_db_value(row[0]).rstrip("/")
    username = decode_db_value(row[1])
    password = decode_db_value(row[2])
    if not base_url or not username or not password:
        raise BlockedError("opencode_runtime_incomplete", "OpenCode runtime is incomplete. Please reconnect OpenCode in OpenCrew Step 1.")
    return {"base_url": base_url, "username": username, "password": password}


def opencode_request(runtime: dict[str, str], method: str, path: str, payload: dict[str, Any] | None, directory: str, timeout: int = 120) -> Any:
    query = urllib.parse.urlencode({"directory": directory})
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    token = base64.b64encode(f"{runtime['username']}:{runtime['password']}".encode("utf-8")).decode("ascii")
    req = urllib.request.Request(
        f"{runtime['base_url']}{path}?{query}",
        data=data,
        method=method,
        headers={
            "Authorization": f"Basic {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            body = res.read().decode("utf-8", errors="replace")
            stripped = body.strip()
            if not stripped:
                return None
            try:
                return json.loads(stripped)
            except json.JSONDecodeError as exc:
                if method.upper() != "GET":
                    return {"raw_response": stripped[:3000]}
                raise ToolError(f"OpenCode returned non-JSON for {method} {path}: {stripped[:3000]}") from exc
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:3000]
        raise ToolError(f"OpenCode HTTP {exc.code}: {detail}") from exc


def now_ms() -> int:
    return int(time.time() * 1000)


def last_completed_assistant(messages: list[dict[str, Any]], started_after: int) -> str:
    for message in reversed(messages):
        info = message.get("info") or {}
        if info.get("role") != "assistant":
            continue
        completed = int(((info.get("time") or {}).get("completed") or 0) or 0)
        if completed < started_after:
            continue
        texts = [str(part.get("text") or "") for part in (message.get("parts") or []) if part.get("type") == "text"]
        text = "\n".join([item.strip() for item in texts if item.strip()]).strip()
        if text:
            return text
    return ""


def call_opencode_run_model(args: Args, variables: dict[str, Any], config: ModelConfig, prompt_path: Path, parts: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    session_id = text_value(variables.get("opencode_session_id"))
    if not session_id:
        raise BlockedError("opencode_session_id_missing", "SessionContext/Variables.json is missing opencode_session_id.")
    directory = text_value(variables.get("workspace_dir")) or str(prompt_path.parents[2])
    runtime = fetch_opencode_runtime(args)
    prompt_text = prompt_path.read_text(encoding="utf-8")
    payload: dict[str, Any] = {
        "parts": parts if parts is not None else [{"type": "text", "text": prompt_text}],
        "model": {"providerID": config.provider, "modelID": config.model},
    }
    started_at = now_ms()
    try:
        opencode_request(runtime, "POST", f"/session/{urllib.parse.quote(session_id, safe='')}/prompt_async", payload, directory, timeout=30)
    except ToolError as exc:
        if not is_opencode_session_not_found(exc):
            raise
        workspace = Path(directory).expanduser()
        try:
            session_id = recover_opencode_session_id(
                runtime=runtime,
                variables=variables,
                workspace=workspace,
                request_func=opencode_request,
                database_url=resolve_database_url(args),
                title=f"Analysis_V1 task {variables.get('task_id') or ''} SRT rewrite".strip(),
            )
        except Exception as repair_exc:
            raise ToolError(f"OpenCode session was missing and automatic repair failed: {repair_exc}") from exc
        opencode_request(runtime, "POST", f"/session/{urllib.parse.quote(session_id, safe='')}/prompt_async", payload, directory, timeout=30)
    deadline = time.time() + 300
    while time.time() < deadline:
        messages = opencode_request(runtime, "GET", f"/session/{urllib.parse.quote(session_id, safe='')}/message", None, directory, timeout=30) or []
        assistant_text = last_completed_assistant(messages, started_at)
        if assistant_text:
            return parse_json_from_text(assistant_text)
        time.sleep(1)
    raise ToolError("OpenCode run model timed out before returning a completed assistant message.")

def call_text_model(args: Args, config: ModelConfig, prompt_path: Path) -> dict[str, Any]:
    raise ToolError("call_text_model requires variables; use call_text_model_with_variables.")


def call_text_model_with_variables(args: Args, variables: dict[str, Any], config: ModelConfig, prompt_path: Path) -> dict[str, Any]:
    return call_opencode_run_model(args, variables, config, prompt_path)


def parse_json_from_text(text: str) -> dict[str, Any]:
    clean = text.strip()
    if clean.startswith("```"):
        clean = re.sub(r"^```(?:json)?", "", clean).strip()
        clean = re.sub(r"```$", "", clean).strip()
    start = clean.find("{")
    end = clean.rfind("}")
    if start >= 0 and end > start:
        clean = clean[start : end + 1]
    payload = json.loads(clean)
    if not isinstance(payload, dict):
        raise ToolError("Model response JSON must be an object.")
    return payload


def clean_dialogue(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"^\s*\d+[\.\、\)]\s*", "", text)
    return text.strip()


def minimal_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "srt_id": text_value(item.get("srt_id")),
        "start": item.get("start"),
        "end": item.get("end"),
        "duration": item.get("duration"),
        "image_path": text_value(item.get("image_path")),
        "dialogue": text_value(item.get("dialogue")),
    }


def validate_input_items(items: list[dict[str, Any]]) -> None:
    seen: set[str] = set()
    for index, item in enumerate(items, 1):
        srt_id = text_value(item.get("srt_id"))
        if not srt_id:
            raise BlockedError("input_srt_id_missing", f"Input item #{index} is missing srt_id.")
        if srt_id in seen:
            raise BlockedError("input_srt_id_duplicate", f"Input contains duplicate srt_id: {srt_id}")
        seen.add(srt_id)
        if not text_value(item.get("dialogue")):
            raise BlockedError("input_dialogue_missing", f"Input item {srt_id} has empty dialogue.")


def build_rewrite_prompt(variables: dict[str, Any], source_items: list[dict[str, Any]], rewrite_final_prompt: str, prompt_source: str) -> str:
    context = business_context(variables)
    input_payload = {"items": [minimal_item(item) for item in source_items]}
    return f"""# SRT Rewrite Prompt

## Business Rewrite Final Prompt
{rewrite_final_prompt}

## Prompt Source
{prompt_source}

## Business Context
{json.dumps(context, ensure_ascii=False, indent=2)}

## Task
请把 Input Items 中每一句原对白逐句头对头改写成新产品/新主题版本。

## Hard Rules
1. 必须保持输入句子数量完全一致。
2. 必须保持 srt_id 完全一致，并按输入顺序输出。
3. 每个输入句子只能输出一个对应改写句子。
4. 不得合并任何两句。
5. 不得拆分任何一句。
6. 不得新增句子。
7. 不得删除句子。
8. 不得改变 start/end/duration/image_path。
9. 只改写 dialogue 的文本内容。
10. 改写要尽量保持原句口播长度、节奏和语气功能，方便后续继续对应原图片帧和时间轴。
11. 输出对白必须使用简体中文，禁止繁体字；英文、数字、品牌名保持原样。

## Input Items
{json.dumps(input_payload, ensure_ascii=False, indent=2)}

## Required Output JSON
只输出严格 JSON，不要 Markdown，不要解释，不要代码块。

{{
  "items": [
    {{
      "srt_id": "srt_0001_01",
      "rewritten_dialogue": "改写后的对白",
      "rewrite_notes": "可选，简短说明替换了什么"
    }}
  ]
}}
"""


def build_repair_prompt(source_items: list[dict[str, Any]], bad_response: dict[str, Any], issues: list[str]) -> str:
    expected_ids = [text_value(item.get("srt_id")) for item in source_items]
    return f"""# SRT Rewrite Repair Prompt

你上一次输出没有通过校验。请只修复 JSON 结构和逐句对应关系，不要改变任务约束。

硬性语言要求：所有 rewritten_dialogue 必须使用简体中文，禁止繁体字；英文、数字、品牌名保持原样。

## Validation Issues
{json.dumps(issues, ensure_ascii=False, indent=2)}

## Expected srt_id Order
{json.dumps(expected_ids, ensure_ascii=False, indent=2)}

## Previous Response
{json.dumps(bad_response, ensure_ascii=False, indent=2)}

## Required Output JSON
只输出严格 JSON，不要 Markdown，不要解释，不要代码块。

{{
  "items": [
    {{
      "srt_id": "srt_0001_01",
      "rewritten_dialogue": "改写后的对白",
      "rewrite_notes": "可选，简短说明替换了什么"
    }}
  ]
}}
"""


def model_items(response: dict[str, Any]) -> list[dict[str, Any]]:
    items = response.get("items")
    if not isinstance(items, list):
        rows = response.get("rows")
        items = rows if isinstance(rows, list) else []
    return [item for item in items if isinstance(item, dict)]


def validate_model_response(response: dict[str, Any], source_items: list[dict[str, Any]]) -> list[str]:
    issues: list[str] = []
    items = model_items(response)
    expected_ids = [text_value(item.get("srt_id")) for item in source_items]
    actual_ids = [text_value(item.get("srt_id")) for item in items]
    if len(items) != len(source_items):
        issues.append(f"Item count mismatch: expected {len(source_items)}, got {len(items)}.")
    if actual_ids != expected_ids:
        issues.append("srt_id order mismatch or missing ids.")
    seen: set[str] = set()
    for index, item in enumerate(items):
        srt_id = text_value(item.get("srt_id"))
        if not srt_id:
            issues.append(f"Output item #{index + 1} is missing srt_id.")
            continue
        if srt_id in seen:
            issues.append(f"Duplicate output srt_id: {srt_id}.")
        seen.add(srt_id)
        dialogue = clean_dialogue(item.get("rewritten_dialogue") or item.get("dialogue") or item.get("new_text") or item.get("text"))
        if not dialogue:
            issues.append(f"Output item {srt_id} has empty rewritten_dialogue.")
        elif contains_traditional_chinese(dialogue):
            issues.append(f"Output item {srt_id} contains Traditional Chinese; rewritten_dialogue must use Simplified Chinese.")
    return issues


def normalize_model_response_to_simplified(response: dict[str, Any]) -> int:
    changed = 0
    try:
        for item in model_items(response):
            for field in ("rewritten_dialogue", "dialogue", "new_text", "text"):
                if field not in item or item.get(field) is None:
                    continue
                before = str(item.get(field) or "")
                after = to_simplified_chinese(before)
                if after != before:
                    item[field] = after
                    changed += 1
    except SimplifiedChineseError as exc:
        raise BlockedError("simplified_chinese_normalizer_missing", str(exc)) from exc
    return changed


def record_simplified_normalization(result: dict[str, Any], changed: int) -> None:
    if changed <= 0:
        return
    result.setdefault("warnings", []).append({
        "code": "srt_rewrite_text_normalized_to_simplified_chinese",
        "message": f"Normalized {changed} rewritten SRT text field(s) to Simplified Chinese.",
    })


def merge_rewritten_items(response: dict[str, Any], source_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {text_value(item.get("srt_id")): item for item in model_items(response)}
    merged: list[dict[str, Any]] = []
    for source in source_items:
        srt_id = text_value(source.get("srt_id"))
        row = by_id[srt_id]
        dialogue = to_simplified_chinese(clean_dialogue(row.get("rewritten_dialogue") or row.get("dialogue") or row.get("new_text") or row.get("text")))
        merged.append({
            "srt_id": srt_id,
            "dialogue": dialogue,
            "original_dialogue": to_simplified_chinese(text_value(source.get("dialogue"))),
            "image_path": text_value(source.get("image_path")),
            "start": float(source.get("start") or 0.0),
            "end": float(source.get("end") or source.get("start") or 0.0),
            "duration": float(source.get("duration") or max(0.0, float(source.get("end") or 0.0) - float(source.get("start") or 0.0))),
        })
    return merged


def item_float(value: Any, fallback: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return number if number == number else fallback


def original_dialogue_passthrough_items(source_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    for source in source_items:
        start = item_float(source.get("start"), 0.0)
        end = item_float(source.get("end"), start)
        duration = item_float(source.get("duration"), max(0.0, end - start))
        if end < start:
            end = start
        dialogue = text_value(source.get("dialogue"))
        merged.append({
            "srt_id": text_value(source.get("srt_id")),
            "dialogue": dialogue,
            "original_dialogue": dialogue,
            "image_path": text_value(source.get("image_path")),
            "start": start,
            "end": end,
            "duration": duration,
        })
    return merged


def is_original_dialogue_passthrough_payload(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    return (
        payload.get("passthrough_original_dialogue") is True
        or text_value(payload.get("identity_policy")) == ORIGINAL_DIALOGUE_PASSTHROUGH_POLICY
    )


def normalize_final_payload_to_simplified(payload: dict[str, Any]) -> int:
    changed = 0
    try:
        for item in payload.get("items") or []:
            if not isinstance(item, dict):
                continue
            for field in ("dialogue", "original_dialogue"):
                if field not in item or item.get(field) is None:
                    continue
                before = str(item.get(field) or "")
                after = to_simplified_chinese(before)
                if after != before:
                    item[field] = after
                    changed += 1
    except SimplifiedChineseError as exc:
        raise BlockedError("simplified_chinese_normalizer_missing", str(exc)) from exc
    return changed


def srt_timestamp(seconds: float) -> str:
    clean = max(0.0, float(seconds or 0.0))
    hours = int(clean // 3600)
    minutes = int((clean % 3600) // 60)
    whole = int(clean % 60)
    millis = int(round((clean - int(clean)) * 1000))
    if millis >= 1000:
        whole += 1
        millis -= 1000
    return f"{hours:02d}:{minutes:02d}:{whole:02d},{millis:03d}"


def build_srt(items: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for index, item in enumerate(items, 1):
        lines.extend([
            str(index),
            f"{srt_timestamp(float(item.get('start') or 0.0))} --> {srt_timestamp(float(item.get('end') or 0.0))}",
            text_value(item.get("dialogue")),
            "",
        ])
    return "\n".join(lines).strip() + "\n"


def build_final_payload(variables: dict[str, Any], prompt_source: str, config: ModelConfig, source_items: list[dict[str, Any]], merged_items: list[dict[str, Any]]) -> dict[str, Any]:
    rewrite_final_prompt, _ = resolve_rewrite_prompt(variables)
    return {
        "schema_version": "analysis_v1_rewritten_srt_items_0.1",
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "source_items_path": SESSION_FINAL_ITEMS_REL,
        "prompt_source": prompt_source,
        "rewrite_final_prompt_sha256": hashlib.sha256(rewrite_final_prompt.encode("utf-8")).hexdigest(),
        "business_context": business_context(variables),
        "model": {"provider": config.provider, "model": config.model, "source": config.source},
        "identity_policy": "srt_id_count_order_time_image_path_preserved",
        "items": merged_items,
        "created_at": now_iso(),
    }


def build_original_dialogue_passthrough_payload(
    variables: dict[str, Any],
    prompt_source: str,
    source_items: list[dict[str, Any]],
    merged_items: list[dict[str, Any]],
    *,
    tool_name: str = TOOL_NAME,
    tool_version: str = TOOL_VERSION,
    schema_version: str = "analysis_v1_rewritten_srt_items_0.1",
) -> dict[str, Any]:
    rewrite_final_prompt, _ = resolve_rewrite_prompt(variables)
    return {
        "schema_version": schema_version,
        "tool": tool_name,
        "tool_version": tool_version,
        "source_items_path": SESSION_FINAL_ITEMS_REL,
        "prompt_source": prompt_source,
        "rewrite_final_prompt_sha256": hashlib.sha256(rewrite_final_prompt.encode("utf-8")).hexdigest(),
        "business_context": business_context(variables),
        "model": {"provider": "", "model": "", "source": "not_used_original_dialogue_passthrough"},
        "identity_policy": ORIGINAL_DIALOGUE_PASSTHROUGH_POLICY,
        "passthrough_original_dialogue": True,
        "source_item_count": len(source_items),
        "items": merged_items,
        "created_at": now_iso(),
    }


def run_rewrite(workspace: Path, args: Args, variables: dict[str, Any], final_items_payload: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    source_items = [item for item in final_items_payload.get("items", []) if isinstance(item, dict)]
    validate_input_items(source_items)
    rewrite_final_prompt, prompt_source = resolve_rewrite_prompt(variables)
    if wants_original_dialogue_passthrough(variables):
        merged_items = original_dialogue_passthrough_items(source_items)
        final_payload = build_original_dialogue_passthrough_payload(variables, prompt_source, source_items, merged_items)
        write_json(workspace / OUTPUT_REWRITTEN_ITEMS_REL, final_payload)
        write_json(workspace / SESSION_REWRITTEN_ITEMS_REL, final_payload)
        write_text(workspace / SESSION_REWRITTEN_SRT_REL, build_srt(merged_items))

        result["status"] = "completed"
        result["requires_model_calls"] = False
        result["inputs"] = {
            "variables": VARIABLES_REL,
            "final_srt_frame_items": SESSION_FINAL_ITEMS_REL,
        }
        result["outputs"] = {
            "rewritten_srt_items": SESSION_REWRITTEN_ITEMS_REL,
            "rewritten_dialogue_srt": SESSION_REWRITTEN_SRT_REL,
        }
        result["counts"] = {
            "input_items": len(source_items),
            "rewritten_items": len(merged_items),
            "passthrough_original_dialogue": 1,
            "repair_calls": 0,
        }
        result["warnings"].append({
            "code": "original_dialogue_passthrough",
            "message": "Rewrite prompt requested the original video script, so rewritten SRT was copied from the original SRT without a model rewrite.",
        })
        result["created_files"] = [
            WORKING_VARIABLES_REL,
            WORKING_FINAL_ITEMS_REL,
            OUTPUT_REWRITTEN_ITEMS_REL,
            SESSION_REWRITTEN_ITEMS_REL,
            SESSION_REWRITTEN_SRT_REL,
            REPORT_RESULT_REL,
        ]
        return final_payload
    config = resolve_model_config(args, variables)

    prompt_path = workspace / REWRITE_PROMPT_REL
    write_text_if_needed(prompt_path, build_rewrite_prompt(variables, source_items, rewrite_final_prompt, prompt_source), args.force_regenerate_prompts or args.force)
    response = call_text_model_with_variables(args, variables, config, prompt_path)
    record_simplified_normalization(result, normalize_model_response_to_simplified(response))
    write_json(workspace / OUTPUT_MODEL_RESPONSE_REL, response)
    issues = validate_model_response(response, source_items)

    repair_calls = 0
    if issues and int(args.max_repair_attempts) > 0:
        repair_calls = 1
        repair_path = workspace / REPAIR_PROMPT_REL
        write_text(repair_path, build_repair_prompt(source_items, response, issues))
        response = call_text_model_with_variables(args, variables, config, repair_path)
        record_simplified_normalization(result, normalize_model_response_to_simplified(response))
        write_json(workspace / OUTPUT_MODEL_RESPONSE_REL, response)
        issues = validate_model_response(response, source_items)

    if issues:
        raise BlockedError("srt_rewrite_model_output_invalid", "Model output failed validation: " + " | ".join(issues[:8]))

    merged_items = merge_rewritten_items(response, source_items)
    final_payload = build_final_payload(variables, prompt_source, config, source_items, merged_items)
    write_json(workspace / OUTPUT_REWRITTEN_ITEMS_REL, final_payload)
    write_json(workspace / SESSION_REWRITTEN_ITEMS_REL, final_payload)
    write_text(workspace / SESSION_REWRITTEN_SRT_REL, build_srt(merged_items))

    result["status"] = "completed"
    result["inputs"] = {
        "variables": VARIABLES_REL,
        "final_srt_frame_items": SESSION_FINAL_ITEMS_REL,
    }
    result["outputs"] = {
        "rewritten_srt_items": SESSION_REWRITTEN_ITEMS_REL,
        "rewritten_dialogue_srt": SESSION_REWRITTEN_SRT_REL,
    }
    result["counts"] = {
        "input_items": len(source_items),
        "rewritten_items": len(merged_items),
        "repair_calls": repair_calls,
    }
    result["created_files"] = [
        WORKING_VARIABLES_REL,
        WORKING_FINAL_ITEMS_REL,
        REWRITE_PROMPT_REL,
        OUTPUT_MODEL_RESPONSE_REL,
        OUTPUT_REWRITTEN_ITEMS_REL,
        SESSION_REWRITTEN_ITEMS_REL,
        SESSION_REWRITTEN_SRT_REL,
        REPORT_RESULT_REL,
    ]
    if repair_calls:
        result["created_files"].insert(3, REPAIR_PROMPT_REL)
    return final_payload


def reusable_rewrite_output(existing_payload: Any, variables: dict[str, Any]) -> tuple[bool, str]:
    if not isinstance(existing_payload, dict):
        return False, "existing_rewrite_output_invalid"
    wants_passthrough = wants_original_dialogue_passthrough(variables)
    existing_passthrough = is_original_dialogue_passthrough_payload(existing_payload)
    if wants_passthrough and not existing_passthrough:
        return False, "existing_rewrite_output_not_original_passthrough"
    if not wants_passthrough and existing_passthrough:
        return False, "existing_rewrite_output_is_original_passthrough"
    rewrite_final_prompt, prompt_source = resolve_rewrite_prompt(variables)
    expected_prompt_hash = hashlib.sha256(rewrite_final_prompt.encode("utf-8")).hexdigest()
    existing_prompt_hash = text_value(existing_payload.get("rewrite_final_prompt_sha256"))
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
                changed = 0
                if not is_original_dialogue_passthrough_payload(final_payload):
                    changed = normalize_final_payload_to_simplified(final_payload)
                    record_simplified_normalization(result, changed)
                    if changed:
                        write_json(workspace / SESSION_REWRITTEN_ITEMS_REL, final_payload)
                        write_json(workspace / OUTPUT_REWRITTEN_ITEMS_REL, final_payload)
                        write_text(workspace / SESSION_REWRITTEN_SRT_REL, build_srt(final_payload.get("items") or []))
                result["status"] = "completed"
                result["requires_model_calls"] = not is_original_dialogue_passthrough_payload(final_payload)
                result["outputs"] = {"rewritten_srt_items": SESSION_REWRITTEN_ITEMS_REL, "rewritten_dialogue_srt": SESSION_REWRITTEN_SRT_REL}
                result["counts"] = {"rewritten_items": len(final_payload.get("items") or []), "reused": 1}
                result["warnings"].append({"code": "reused_completed_output", "message": "Existing rewritten SRT output was reused."})
            else:
                result["warnings"].append({"code": "rewrite_output_stale", "message": f"Existing rewritten SRT output was not reused because {stale_reason}."})
                run_rewrite(workspace, args, variables, final_items, result)
        else:
            run_rewrite(workspace, args, variables, final_items, result)
    except BlockedError as exc:
        add_block(result, exc.code, exc.message)
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
    parser = argparse.ArgumentParser(description="Rewrite Analysis_V1 final SRT-frame items sentence-by-sentence while preserving srt_id/time/frame bindings.")
    parser.add_argument("--workspace", default="", help="Analysis_V1 workspace. Defaults to current working directory.")
    parser.add_argument("--model-provider", default="", help="Text model provider override. Defaults to SessionContext run_model_provider.")
    parser.add_argument("--model-id", default="", help="Text model id override. Defaults to SessionContext run_model_id.")
    parser.add_argument("--database-url", default="")
    parser.add_argument("--database-url-env", default=DEFAULT_DATABASE_URL_ENV)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--force-regenerate-prompts", action="store_true")
    parser.add_argument("--max-repair-attempts", type=int, default=1)
    parser.add_argument("--print-json", action="store_true")
    ns = parser.parse_args(argv)
    return Args(
        workspace=str(ns.workspace or ""),
        model_provider=str(ns.model_provider or ""),
        model_id=str(ns.model_id or ""),
        database_url=str(ns.database_url or ""),
        database_url_env=str(ns.database_url_env or DEFAULT_DATABASE_URL_ENV),
        force=bool(ns.force),
        resume=bool(ns.resume),
        force_regenerate_prompts=bool(ns.force_regenerate_prompts),
        max_repair_attempts=max(0, int(ns.max_repair_attempts)),
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
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"{TOOL_NAME} {result['status']}: {result.get('outputs', {}).get('rewritten_srt_items', '')}")
    return 0 if result["status"] in {"completed", "blocked"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
