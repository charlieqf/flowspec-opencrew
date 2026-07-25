from __future__ import annotations

import argparse
import base64
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


TOOL_NAME = "04_02_StoryBoard"
TOOL_VERSION = "0.1.0"
CONTEXT_DIR_NAME = "SessionContext"
VARIABLES_REL = f"{CONTEXT_DIR_NAME}/Variables.json"
CONSISTENCY_DIR_REL = f"{CONTEXT_DIR_NAME}/Consistency"
TOOL_DIR_NAME = "S7_04_02_StoryBoard"
WORKING_VARIABLES_REL = f"{TOOL_DIR_NAME}/Working/InputFrom_0_Variables.json"
WORKING_REWRITTEN_ITEMS_REL = f"{TOOL_DIR_NAME}/Working/InputFrom_6_rewritten_srt_items.json"
PROMPT_DIR_REL = f"{TOOL_DIR_NAME}/Prompt"
STORYBOARD_PROMPT_REL = f"{PROMPT_DIR_REL}/00_storyboard_prompt.md"
REPAIR_PROMPT_REL = f"{PROMPT_DIR_REL}/01_storyboard_repair_prompt.md"
OUTPUT_MODEL_RESPONSE_REL = f"{TOOL_DIR_NAME}/Output/model_response.json"
OUTPUT_STORYBOARD_REL = f"{TOOL_DIR_NAME}/Output/srt_storyboard.json"
REPORT_RESULT_REL = f"{TOOL_DIR_NAME}/Report/Result.json"
SESSION_REWRITTEN_ITEMS_REL = "SessionOutput/subtitle/rewritten_srt_items.json"
SESSION_STORYBOARD_DIR_REL = "SessionOutput/storyboard"
SESSION_STORYBOARD_REL = f"{SESSION_STORYBOARD_DIR_REL}/srt_storyboard.json"
SESSION_STORYBOARD_ASSETS_IMAGES_DIR_REL = f"{SESSION_STORYBOARD_DIR_REL}/assets/images"
SESSION_STORYBOARD_ASSETS_VIDEOS_DIR_REL = f"{SESSION_STORYBOARD_DIR_REL}/assets/videos"
SESSION_STORYBOARD_WORKING_DIR_REL = f"{SESSION_STORYBOARD_DIR_REL}/Working"
LEGACY_STORYBOARD_LAYOUT_DIRS = (
    f"{SESSION_STORYBOARD_DIR_REL}/shots",
    f"{SESSION_STORYBOARD_DIR_REL}/scenes",
)
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


def update_json_file(path: Path, patch: dict[str, Any]) -> dict[str, Any]:
    payload = read_json(path) if path.exists() else {}
    if not isinstance(payload, dict):
        payload = {}
    payload.update(patch)
    write_json(path, payload)
    return payload


def remove_path(path: Path) -> None:
    if not path.exists():
        return
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


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


def text_value(value: Any) -> str:
    return str(value or "").strip()


def dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def load_variables(workspace: Path) -> dict[str, Any]:
    path = workspace / VARIABLES_REL
    if not path.exists():
        raise BlockedError("variables_missing", f"Required SessionContext file is missing: {VARIABLES_REL}.")
    payload = read_json(path)
    if not isinstance(payload, dict):
        raise BlockedError("variables_invalid", f"{VARIABLES_REL} must contain a JSON object.")
    return payload


def load_rewritten_items(workspace: Path) -> dict[str, Any]:
    path = workspace / SESSION_REWRITTEN_ITEMS_REL
    if not path.exists():
        raise BlockedError("rewritten_srt_items_missing", f"Required rewritten SRT JSON is missing: {SESSION_REWRITTEN_ITEMS_REL}. Run 04_01_SRTRewrite.py first.")
    payload = read_json(path)
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise BlockedError("rewritten_srt_items_invalid", f"{SESSION_REWRITTEN_ITEMS_REL} must contain a JSON object with items.")
    if not payload["items"]:
        raise BlockedError("rewritten_srt_items_empty", f"{SESSION_REWRITTEN_ITEMS_REL} contains no dialogue items.")
    return payload


def ensure_dirs(workspace: Path) -> None:
    for rel in (
        CONSISTENCY_DIR_REL,
        f"{TOOL_DIR_NAME}/Working",
        f"{TOOL_DIR_NAME}/Output",
        PROMPT_DIR_REL,
        f"{TOOL_DIR_NAME}/Report",
        SESSION_STORYBOARD_DIR_REL,
        SESSION_STORYBOARD_ASSETS_IMAGES_DIR_REL,
        SESSION_STORYBOARD_ASSETS_VIDEOS_DIR_REL,
        SESSION_STORYBOARD_WORKING_DIR_REL,
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
            "visual_inputs": "not_used",
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
    for rel in (TOOL_DIR_NAME, SESSION_STORYBOARD_REL, *LEGACY_STORYBOARD_LAYOUT_DIRS):
        path = workspace / rel
        if path.exists():
            remove_path(path)
            result.setdefault("cleanup_actions", []).append({"path": rel, "action": "removed_for_force_rerun"})


def business_context(variables: dict[str, Any]) -> dict[str, str]:
    explicit = dict_value(variables.get("business_context"))
    keys = ("industry", "persona", "target_audience", "product_info", "constraints", "video_formula")
    return {key: text_value(explicit.get(key) or variables.get(key)) for key in keys}


def resolve_storyboard_prompt(variables: dict[str, Any]) -> tuple[str, str]:
    storyboard = dict_value(variables.get("storyboard_prompt"))
    prompt = text_value(storyboard.get("final_prompt")) or text_value(variables.get("storyboard_final_prompt"))
    source = (
        text_value(storyboard.get("source"))
        or ("SessionContext/Variables.json:storyboard_prompt.final_prompt" if text_value(storyboard.get("final_prompt")) else "")
        or ("SessionContext/Variables.json:storyboard_final_prompt" if text_value(variables.get("storyboard_final_prompt")) else "")
    )
    if not prompt:
        raise BlockedError("storyboard_final_prompt_missing", "04_02_StoryBoard requires storyboard_prompt.final_prompt or storyboard_final_prompt in SessionContext/Variables.json.")
    return prompt, source


def resolve_model_config(args: Args, variables: dict[str, Any]) -> ModelConfig:
    storyboard_model = dict_value(variables.get("storyboard_model_config"))
    provider = (
        text_value(args.model_provider)
        or text_value(storyboard_model.get("provider"))
        or text_value(variables.get("run_model_provider"))
    )
    model = (
        text_value(args.model_id)
        or text_value(storyboard_model.get("model"))
        or text_value(variables.get("run_model_id"))
    )
    if not provider or not model:
        raise BlockedError(
            "text_model_config_missing",
            "04_02_StoryBoard requires a run model. Provide --model-provider/--model-id or set run_model_provider/run_model_id in SessionContext/Variables.json.",
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


def call_opencode_run_model(args: Args, variables: dict[str, Any], config: ModelConfig, prompt_path: Path) -> dict[str, Any]:
    session_id = text_value(variables.get("opencode_session_id"))
    if not session_id:
        raise BlockedError("opencode_session_id_missing", "SessionContext/Variables.json is missing opencode_session_id.")
    directory = text_value(variables.get("workspace_dir")) or str(prompt_path.parents[2])
    runtime = fetch_opencode_runtime(args)
    prompt_text = prompt_path.read_text(encoding="utf-8")
    payload: dict[str, Any] = {
        "parts": [{"type": "text", "text": prompt_text}],
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
                title=f"Analysis_V1 task {variables.get('task_id') or ''} storyboard".strip(),
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


def float_value(value: Any) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


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
    previous_start = -1.0
    for index, item in enumerate(items, 1):
        srt_id = text_value(item.get("srt_id"))
        if not srt_id:
            raise BlockedError("input_srt_id_missing", f"Input item #{index} is missing srt_id.")
        if srt_id in seen:
            raise BlockedError("input_srt_id_duplicate", f"Input contains duplicate srt_id: {srt_id}")
        seen.add(srt_id)
        if not text_value(item.get("dialogue")):
            raise BlockedError("input_dialogue_missing", f"Input item {srt_id} has empty dialogue.")
        start = float_value(item.get("start"))
        if start < previous_start:
            raise BlockedError("input_time_order_invalid", f"Input item {srt_id} starts before the previous item.")
        previous_start = start


def build_storyboard_prompt(variables: dict[str, Any], source_items: list[dict[str, Any]], storyboard_final_prompt: str, prompt_source: str) -> str:
    context = business_context(variables)
    input_payload = {"items": [minimal_item(item) for item in source_items]}
    return f"""# SRT StoryBoard Prompt

## StoryBoard Final Prompt
{storyboard_final_prompt}

## Prompt Source
{prompt_source}

## Business Context
{json.dumps(context, ensure_ascii=False, indent=2)}

## Task
请基于 StoryBoard Final Prompt 和 Input Items，把改写后的 SRT 组织为 Shot / Scene 结构。
同时输出 task_summary：用一句简短中文总结这个口播任务讲什么，优先体现产品信息、主题、目标受众和核心表达方向。

## Hard Rules
1. 不要改写任何 dialogue。
2. 不要改变任何 srt_id、start、end、duration、image_path。
3. 每个输入 srt_id 必须出现且只出现一次。
4. 不得合并、拆分、删除、新增任何 SRT 句子。
5. Shot 和 Scene 都必须使用连续的 srt_id 范围，不能倒序，不能交叉。
6. 每个 Scene 必须归属一个 Shot。
7. 分组目标、视频公式、语义优先级和节奏要求以 StoryBoard Final Prompt 为准；不要使用代码外的隐藏规则。
8. 不需要看图片，也不要输出新的图片路径；只根据文本、时间和 image_path 字段组织结构。
9. 模型只输出分组结构；时间、时长、逐条 dialogue_items 和 key_frame_paths 将由工具回填。

## Input Items
{json.dumps(input_payload, ensure_ascii=False, indent=2)}

## Required Output JSON
只输出严格 JSON，不要 Markdown，不要解释，不要代码块。

{{
  "task_summary": "40-80 个中文字符的一句话任务简介，说明这个口播主要讲什么",
  "video_formula": "来自提示词或业务上下文的视频公式",
  "shots": [
    {{
      "shot_id": "shot_001",
      "title": "可选；如果没有标题可省略",
      "formula_stage": "可选；例如 Hook/Trust/CTA 或提示词定义的阶段",
      "summary": "这个 shot 的结构功能摘要",
      "srt_ids": ["srt_0001_01"],
      "scenes": [
        {{
          "scene_id": "scene_001",
          "title": "可选；如果没有标题可省略",
          "summary": "这个 scene 的表达摘要",
          "srt_ids": ["srt_0001_01"]
        }}
      ]
    }}
  ]
}}
"""


def build_repair_prompt(source_items: list[dict[str, Any]], bad_response: dict[str, Any], issues: list[str]) -> str:
    expected_ids = [text_value(item.get("srt_id")) for item in source_items]
    return f"""# SRT StoryBoard Repair Prompt

你上一次输出没有通过校验。请只修复 JSON 结构和 Shot / Scene 的 srt_id 分组，不要改写对白，不要改变任何 srt_id。

## Validation Issues
{json.dumps(issues, ensure_ascii=False, indent=2)}

## Expected srt_id Order
{json.dumps(expected_ids, ensure_ascii=False, indent=2)}

## Previous Response
{json.dumps(bad_response, ensure_ascii=False, indent=2)}

## Required Output JSON
只输出严格 JSON，不要 Markdown，不要解释，不要代码块。

{{
  "task_summary": "一句话任务简介",
  "video_formula": "来自提示词或业务上下文的视频公式",
  "shots": [
    {{
      "shot_id": "shot_001",
      "title": "可选",
      "formula_stage": "可选",
      "summary": "摘要",
      "srt_ids": ["srt_0001_01"],
      "scenes": [
        {{
          "scene_id": "scene_001",
          "title": "可选",
          "summary": "摘要",
          "srt_ids": ["srt_0001_01"]
        }}
      ]
    }}
  ]
}}
"""


def as_id_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [text_value(item) for item in value if text_value(item)]
    return []


def model_shots(response: dict[str, Any]) -> list[dict[str, Any]]:
    shots = response.get("shots")
    if not isinstance(shots, list):
        return []
    return [item for item in shots if isinstance(item, dict)]


def model_scenes(shot: dict[str, Any]) -> list[dict[str, Any]]:
    scenes = shot.get("scenes")
    if not isinstance(scenes, list):
        return []
    return [item for item in scenes if isinstance(item, dict)]


def contiguous(ids: list[str], expected_index: dict[str, int]) -> bool:
    if not ids:
        return False
    positions = [expected_index.get(srt_id, -999999) for srt_id in ids]
    if any(position < 0 for position in positions):
        return False
    return positions == list(range(positions[0], positions[0] + len(positions)))


def validate_model_response(response: dict[str, Any], source_items: list[dict[str, Any]]) -> list[str]:
    issues: list[str] = []
    if not text_value(response.get("task_summary")):
        issues.append("Missing top-level task_summary.")
    expected_ids = [text_value(item.get("srt_id")) for item in source_items]
    expected_set = set(expected_ids)
    expected_index = {srt_id: index for index, srt_id in enumerate(expected_ids)}
    shots = model_shots(response)
    if not shots:
        issues.append("No shots array was returned.")
        return issues

    all_scene_ids: list[str] = []
    previous_last_index = -1
    for shot_number, shot in enumerate(shots, 1):
        shot_id = text_value(shot.get("shot_id")) or f"shot_{shot_number:03d}"
        scenes = model_scenes(shot)
        if not scenes:
            issues.append(f"{shot_id} has no scenes.")
            continue
        shot_scene_ids: list[str] = []
        for scene_number, scene in enumerate(scenes, 1):
            scene_id = text_value(scene.get("scene_id")) or f"{shot_id}_scene_{scene_number:03d}"
            scene_ids = as_id_list(scene.get("srt_ids"))
            if not scene_ids:
                issues.append(f"{scene_id} has no srt_ids.")
                continue
            unknown = [srt_id for srt_id in scene_ids if srt_id not in expected_set]
            if unknown:
                issues.append(f"{scene_id} contains unknown srt_id(s): {unknown[:5]}.")
            if not contiguous(scene_ids, expected_index):
                issues.append(f"{scene_id} srt_ids are not contiguous or not in input order.")
            shot_scene_ids.extend(scene_ids)
            all_scene_ids.extend(scene_ids)
        if not contiguous(shot_scene_ids, expected_index):
            issues.append(f"{shot_id} scenes are not contiguous or not in input order.")
        shot_ids = as_id_list(shot.get("srt_ids"))
        if shot_ids and shot_ids != shot_scene_ids:
            issues.append(f"{shot_id} srt_ids must equal the concatenated scene srt_ids.")
        if shot_scene_ids:
            first_index = expected_index.get(shot_scene_ids[0], -1)
            last_index = expected_index.get(shot_scene_ids[-1], -1)
            if first_index <= previous_last_index:
                issues.append(f"{shot_id} starts before or overlaps the previous shot.")
            previous_last_index = last_index

    if all_scene_ids != expected_ids:
        missing = [srt_id for srt_id in expected_ids if srt_id not in all_scene_ids]
        duplicates = sorted({srt_id for srt_id in all_scene_ids if all_scene_ids.count(srt_id) > 1})
        if missing:
            issues.append(f"Missing srt_id(s): {missing[:8]}.")
        if duplicates:
            issues.append(f"Duplicate srt_id(s): {duplicates[:8]}.")
        if not missing and not duplicates:
            issues.append("Scene srt_id order does not match input order.")
    return issues


def item_index(source_items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {text_value(item.get("srt_id")): item for item in source_items}


def time_span(ids: list[str], by_id: dict[str, dict[str, Any]]) -> tuple[float, float, float]:
    starts = [float_value(by_id[srt_id].get("start")) for srt_id in ids if srt_id in by_id]
    ends = [float_value(by_id[srt_id].get("end")) for srt_id in ids if srt_id in by_id]
    if not starts or not ends:
        return 0.0, 0.0, 0.0
    start = starts[0]
    end = ends[-1]
    return start, end, max(0.0, end - start)


def dialogue_items_for(ids: list[str], by_id: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for index, srt_id in enumerate(ids):
        item = by_id.get(srt_id)
        if not item:
            continue
        items.append({
            "srt_id": srt_id,
            "dialogue": text_value(item.get("dialogue")),
            "start": item.get("start"),
            "end": item.get("end"),
            "duration": item.get("duration"),
            "image_path": text_value(item.get("image_path")) if index == 0 else "",
        })
    return items


def scene_key_frame_for(ids: list[str], by_id: dict[str, dict[str, Any]]) -> list[str]:
    for srt_id in ids:
        image_path = text_value(by_id.get(srt_id, {}).get("image_path"))
        if image_path:
            return [image_path]
    return []


def shot_key_frames_for(scenes: list[dict[str, Any]]) -> list[str]:
    frames: list[str] = []
    seen: set[str] = set()
    for scene in scenes:
        for image_path in scene.get("key_frame_paths") or []:
            image_text = text_value(image_path)
            if image_text and image_text not in seen:
                frames.append(image_text)
                seen.add(image_text)
    return frames


def normalized_title(value: Any, fallback: str) -> str:
    title = text_value(value)
    return title or fallback


def normalized_shot_id(shot_number: int) -> str:
    return f"shot_{shot_number:03d}"


def normalized_scene_id(scene_number: int) -> str:
    return f"scene_{scene_number:03d}"


def asset_key_for(shot_id: str, scene_id: str) -> str:
    return f"{shot_id}_{scene_id}"


def empty_working_assets() -> dict[str, Any]:
    return {
        "audio": {
            "slot": "Audio_Final",
            "path": "",
        },
        "images": [
            {
                "slot": "Image_New",
                "path": "",
            },
            {
                "slot": "Image_02",
                "path": "",
            },
        ],
        "video": {
            "slot": "Video_Final",
            "path": "",
        },
    }


def prepare_storyboard_asset_layout(workspace: Path, final_payload: dict[str, Any]) -> list[str]:
    prepared = [
        SESSION_STORYBOARD_ASSETS_IMAGES_DIR_REL,
        SESSION_STORYBOARD_ASSETS_VIDEOS_DIR_REL,
        SESSION_STORYBOARD_WORKING_DIR_REL,
    ]
    for rel in prepared:
        (workspace / rel).mkdir(parents=True, exist_ok=True)
    return prepared


def build_final_payload(
    variables: dict[str, Any],
    prompt_source: str,
    config: ModelConfig,
    source_items: list[dict[str, Any]],
    response: dict[str, Any],
) -> dict[str, Any]:
    by_id = item_index(source_items)
    final_shots: list[dict[str, Any]] = []
    scene_global_number = 0
    for shot_number, shot in enumerate(model_shots(response), 1):
        shot_id = normalized_shot_id(shot_number)
        final_scenes: list[dict[str, Any]] = []
        shot_srt_ids: list[str] = []
        for scene_number, scene in enumerate(model_scenes(shot), 1):
            scene_global_number += 1
            scene_id = normalized_scene_id(scene_global_number)
            scene_srt_ids = as_id_list(scene.get("srt_ids"))
            start, end, duration = time_span(scene_srt_ids, by_id)
            asset_key = asset_key_for(shot_id, scene_id)
            final_scenes.append({
                "scene_id": scene_id,
                "title": normalized_title(scene.get("title"), scene_id),
                "summary": text_value(scene.get("summary")),
                "start": start,
                "end": end,
                "duration": duration,
                "srt_ids": scene_srt_ids,
                "dialogue_items": dialogue_items_for(scene_srt_ids, by_id),
                "key_frame_paths": scene_key_frame_for(scene_srt_ids, by_id),
                "asset_key": asset_key,
                "working_assets": empty_working_assets(),
            })
            shot_srt_ids.extend(scene_srt_ids)
        start, end, duration = time_span(shot_srt_ids, by_id)
        final_shots.append({
            "shot_id": shot_id,
            "title": normalized_title(shot.get("title"), shot_id),
            "formula_stage": text_value(shot.get("formula_stage")),
            "summary": text_value(shot.get("summary")),
            "start": start,
            "end": end,
            "duration": duration,
            "srt_ids": shot_srt_ids,
            "key_frame_paths": shot_key_frames_for(final_scenes),
            "scenes": final_scenes,
        })
    return {
        "schema_version": "analysis_v1_srt_storyboard_0.2",
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "task_summary": text_value(response.get("task_summary")),
        "source_items_path": SESSION_REWRITTEN_ITEMS_REL,
        "prompt_source": prompt_source,
        "business_context": business_context(variables),
        "video_formula": text_value(response.get("video_formula")) or business_context(variables).get("video_formula", ""),
        "model": {"provider": config.provider, "model": config.model, "source": config.source},
        "identity_policy": "dialogue_items_are_preserved_per_srt_under_scene; shot_scene_grouping_only",
        "shots": final_shots,
        "created_at": now_iso(),
    }


def run_storyboard(workspace: Path, args: Args, variables: dict[str, Any], rewritten_payload: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    source_items = [item for item in rewritten_payload.get("items", []) if isinstance(item, dict)]
    validate_input_items(source_items)
    storyboard_final_prompt, prompt_source = resolve_storyboard_prompt(variables)
    config = resolve_model_config(args, variables)

    prompt_path = workspace / STORYBOARD_PROMPT_REL
    write_text_if_needed(prompt_path, build_storyboard_prompt(variables, source_items, storyboard_final_prompt, prompt_source), args.force_regenerate_prompts or args.force)
    response = call_opencode_run_model(args, variables, config, prompt_path)
    write_json(workspace / OUTPUT_MODEL_RESPONSE_REL, response)
    issues = validate_model_response(response, source_items)

    repair_calls = 0
    if issues and int(args.max_repair_attempts) > 0:
        repair_calls = 1
        repair_path = workspace / REPAIR_PROMPT_REL
        write_text(repair_path, build_repair_prompt(source_items, response, issues))
        response = call_opencode_run_model(args, variables, config, repair_path)
        write_json(workspace / OUTPUT_MODEL_RESPONSE_REL, response)
        issues = validate_model_response(response, source_items)

    if issues:
        raise BlockedError("storyboard_model_output_invalid", "Model output failed validation: " + " | ".join(issues[:8]))

    final_payload = build_final_payload(variables, prompt_source, config, source_items, response)
    result["prepared_directories"].extend(prepare_storyboard_asset_layout(workspace, final_payload))
    write_json(workspace / OUTPUT_STORYBOARD_REL, final_payload)
    write_json(workspace / SESSION_STORYBOARD_REL, final_payload)
    update_json_file(workspace / VARIABLES_REL, {"task_summary": text_value(final_payload.get("task_summary")), "updated_at": now_iso()})

    scene_count = sum(len(shot.get("scenes") or []) for shot in final_payload.get("shots") or [])
    result["status"] = "completed"
    result["inputs"] = {
        "variables": VARIABLES_REL,
        "rewritten_srt_items": SESSION_REWRITTEN_ITEMS_REL,
    }
    result["outputs"] = {
        "srt_storyboard": SESSION_STORYBOARD_REL,
    }
    result["counts"] = {
        "input_items": len(source_items),
        "shots": len(final_payload.get("shots") or []),
        "scenes": scene_count,
        "working_asset_scenes": scene_count,
        "repair_calls": repair_calls,
    }
    result["created_files"] = [
        WORKING_VARIABLES_REL,
        WORKING_REWRITTEN_ITEMS_REL,
        STORYBOARD_PROMPT_REL,
        OUTPUT_MODEL_RESPONSE_REL,
        OUTPUT_STORYBOARD_REL,
        SESSION_STORYBOARD_REL,
        VARIABLES_REL,
        REPORT_RESULT_REL,
    ]
    if repair_calls:
        result["created_files"].insert(3, REPAIR_PROMPT_REL)
    return final_payload


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
        for rel in (
            CONSISTENCY_DIR_REL,
            f"{TOOL_DIR_NAME}/Working",
            f"{TOOL_DIR_NAME}/Output",
            PROMPT_DIR_REL,
            f"{TOOL_DIR_NAME}/Report",
            SESSION_STORYBOARD_DIR_REL,
            SESSION_STORYBOARD_ASSETS_IMAGES_DIR_REL,
            SESSION_STORYBOARD_ASSETS_VIDEOS_DIR_REL,
            SESSION_STORYBOARD_WORKING_DIR_REL,
        ):
            result["prepared_directories"].append(rel)
        variables = load_variables(workspace)
        rewritten_payload = load_rewritten_items(workspace)
        write_json(workspace / WORKING_VARIABLES_REL, variables)
        write_json(workspace / WORKING_REWRITTEN_ITEMS_REL, rewritten_payload)
        if args.resume and (workspace / SESSION_STORYBOARD_REL).exists() and not args.force:
            final_payload = read_json(workspace / SESSION_STORYBOARD_REL)
            task_summary = text_value(final_payload.get("task_summary")) if isinstance(final_payload, dict) else ""
            if task_summary:
                update_json_file(workspace / VARIABLES_REL, {"task_summary": task_summary, "updated_at": now_iso()})
            result["status"] = "completed"
            result["outputs"] = {"srt_storyboard": SESSION_STORYBOARD_REL}
            result["counts"] = {
                "shots": len(final_payload.get("shots") or []),
                "scenes": sum(len(shot.get("scenes") or []) for shot in final_payload.get("shots") or []),
                "reused": 1,
            }
            result["warnings"].append({"code": "reused_completed_output", "message": "Existing SRT StoryBoard output was reused."})
        else:
            run_storyboard(workspace, args, variables, rewritten_payload, result)
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
    parser = argparse.ArgumentParser(description="Group Analysis_V1 rewritten SRT items into a Shot / Scene storyboard without changing SRT text, timing, or frame bindings.")
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
        print(f"{TOOL_NAME} {result['status']}: {result.get('outputs', {}).get('srt_storyboard', '')}")
    return 0 if result["status"] in {"completed", "blocked"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
