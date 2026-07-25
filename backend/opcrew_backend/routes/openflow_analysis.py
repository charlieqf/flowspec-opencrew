from __future__ import annotations

import json
import shutil
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from ..adapters.opencode import OpenCodeSessionClient
from ..context import AppContext, now_ms
from ..model_policy import (
    SURFACE_OPENFLOW_PROMPT,
    mask_model_fields_for_role,
    mask_prompt_models_for_role,
    request_role,
    resolve_prompt_model_for_role,
)
from ..schemas import OpenFlowAnalysisRunPayload, OpenFlowPromptGeneratePayload, OpenFlowPromptSavePayload, OpenFlowSkillBuilderGeneratePayload, OpenFlowSkillBuilderPayload, OpenFlowSkillVersionDeletePayload, OpenFlowSkillVersionLoadPayload, OpenFlowVersionDeletePayload, OpenFlowVersionLoadPayload, OpenFlowVersionSavePayload


OPENFLOW_SKILL_KIND = "analysis"
OPENFLOW_GROUP_ID = "openflow-analysis"
OPENFLOW_SOURCE = "openflow-analysis"
BACKEND_ROOT = Path(__file__).resolve().parents[2]
BACKEND_VENV_PYTHON = BACKEND_ROOT / ".venv" / "bin" / "python"
OPENFLOW_RUNNER = BACKEND_ROOT / "scripts" / "openflow_analysis_runner.py"
SKILL_BUILDER_REFERENCE = Path(__file__).resolve().parents[1] / "fixtures" / "video_remaster_storyboard_skill.md"

DEFAULT_SIMPLE_PROMPT = (
    "重点关注故事公式、桥段结构、公式的组件槽位、提供详细，均衡，复杂三种拆分方案、"
    "三套对白版本和三套拆分视频。需要进行整体视频的关键帧图片，以及对白的所有文字交给大模型，"
    "按照提示词要求进行视频拆分和分析。"
)

PROMPT_BUILDER_SYSTEM_PROMPT = """
你是 OpenFlow Prompt Builder，只负责把业务输入扩写成一份适合视频拆解分析的业务任务书。

严格要求：
1. 只能输出业务语义、业务判断和业务输出要求。
2. 不得输出任何技术执行细节。
3. 不得出现脚本、命令、工具名、软件名、路径、目录、文件名、命名规则、JSON、Markdown、解释器、依赖、运行顺序。
4. 不得提及 Python、ffmpeg、ffprobe、whisper、scenedetect、runner、workspace、reports、clips、transcripts、storyboards。
5. 不要复述这些禁令，不要解释你的写法。
6. 不要使用代码块。

输出要求：
1. 直接输出最终业务版 Final Prompt。
2. 使用以下结构，并结合输入内容自然扩写：
   - 项目背景
   - 业务目标
   - 拆解视角
   - 业务判断标准
   - 业务输出要求
3. 语言保持专业、清晰、可执行，重点突出视频结构拆解、业务意图、复刻判断标准。
4. 如果输入信息缺失，用稳健的业务表述补足，不要暴露“缺失字段”这种元话语。
""".strip()

SKILL_BUILDER_SYSTEM_PROMPT = """
你是 OpenFlow Skill Builder。你的职责是把最新保存的 Final Prompt 与技术执行工作流合成为一份完整、稳定、可执行的 OpenFlow Analysis Skill。

生成要求：
1. 输出必须是一份可直接用于 OpenCode 执行的视频拆解 skill，不是说明文，不是分析报告。
2. 必须同时覆盖业务目标和技术执行约束。
3. 必须明确要求：先提取关键帧、对白、视频元数据、场景候选切点并写入工作目录，再让大模型浏览关键帧、理解对白、结合节奏与结构完成视频拆解。
4. 必须明确：不能只按视觉切点机械拆分，必须结合关键帧、对白、句子边界、故事动作、拍摄动作和转场。
5. 必须明确：所有关键结果都写入工作目录文件，不得只保留在聊天中。
6. 必须保留技术执行顺序、工具要求、结果包要求、质量校验要求。
7. 必须把执行环境写清楚，包括：会调用哪些工具、工作目录是什么、输入目录是什么、产出目录是什么、关键文件写到哪里。
8. 组件槽位和质量校验必须以当前 video_formula 为准。如果当前公式不是 Hook/Trust/CTA，不得输出 Hook / Trust / CTA 的固定槽位、固定文件名或固定校验项。
9. 必须吸收参考工作流中的“完整工作流、稳定结构、可复用、可交付”写法，但不要提到参考文件来源。
10. 不要使用代码块。

输出结构：
1. 角色定义
2. 工作目标
3. 输入与素材要求
4. 工作目录要求
5. 工具与执行环境
6. 视频理解流程
7. 结构拆解原则
8. 输出产物要求
9. 质量校验要求
10. 最终回答要求
""".strip()

FINAL_PROMPT_TECH_KEYWORDS = [
    "python",
    "ffmpeg",
    "ffprobe",
    "whisper",
    "scenedetect",
    "runner",
    "workspace",
    "reports/",
    "clips/",
    "transcripts/",
    "storyboards/",
    "json",
    "markdown",
    "命名规则",
    "输出目录",
    "脚本",
    "命令",
    "工具名",
    "文件结构",
    "技术执行顺序",
]

INDUSTRY_OPTIONS = ["医美", "大健康", "口腔", "零售", "教育", "餐饮", "家装"]
PERSONA_OPTIONS = ["强判断老板型", "创始人", "门店总经理", "运营负责人", "专家型主理人"]
TARGET_AUDIENCE_OPTIONS = ["老板", "管理者", "潜在客户", "高净值客户", "加盟商"]
ANALYSIS_GOAL_OPTIONS = ["提取整体公式", "拆解商投结构", "拆解分镜与对白", "评估组件化复刻适配度"]
VIDEO_FORMULA_OPTIONS = ["Hook/Trust/CTA", "老板巡店冲突型", "问题-过程-方案型", "反常识抓钩型"]

DEFAULT_OPENFLOW_SKILL = {
    "title": "OpenFlow Analysis Skill",
}


def build_openflow_router(ctx: AppContext) -> APIRouter:
    router = APIRouter()

    def formula_slot_requirement(video_formula: str) -> str:
        formula = video_formula.strip() or "当前视频公式"
        if formula == "Hook/Trust/CTA":
            return (
                "- 必须输出 Hook / Trust / CTA 组件映射，说明每个槽位对应的时间段、结构作用、对白、镜头依据和复刻判断。\n"
                "- 质量校验时必须确认 Hook / Trust / CTA 映射完整。\n"
            )
        return (
            f"- 必须输出与“{formula}”一致的组件槽位映射，槽位命名、解释和校验都必须围绕当前公式，不得套用 Hook / Trust / CTA。\n"
            f"- 质量校验时必须确认“{formula}”对应的组件槽位映射完整。\n"
        )

    def build_openflow_skill_content(video_formula: str = "") -> str:
        return (
            "你是 OpenFlow Analysis Skill。你的职责是完成视频解析与结构拆解，而不是做行业迁移或交付美化。\n\n"
            "执行环境与工具要求：\n"
            f"- Python 解释器：优先使用 {BACKEND_VENV_PYTHON}\n"
            f"- 主分析脚本：优先直接运行 {OPENFLOW_RUNNER}\n"
            "- 关键工具：ffmpeg / ffprobe、ASR 工具、PySceneDetect 或等价场景切点工具。\n"
            "- 如果系统没有 ffmpeg/ffprobe，请优先通过 imageio_ffmpeg 获取可执行文件路径，避免依赖系统 PATH。\n"
            "- 除非主分析脚本失败，否则不要自己临时拼装另一套分析流程。\n\n"
            "工作目录要求：\n"
            "- 工作目录就是当前 session workspace。\n"
            "- 输入目录：workspace/input\n"
            "- 元数据目录：workspace/meta\n"
            "- 对白目录：workspace/transcripts\n"
            "- 分镜目录：workspace/storyboards\n"
            "- 关键帧目录：workspace/keyframes\n"
            "- 拆分视频目录：workspace/clips\n"
            "- 报告目录：workspace/reports\n"
            "- 所有关键结果都必须写回上述目录，不能只留在聊天里。\n\n"
            "执行顺序：\n"
            "1. 读取工作区中的输入文件和最终复杂提示词。\n"
            "2. 检查参考视频是否存在并可读取。\n"
            "3. 使用 ffprobe 提取视频元数据并写入 meta。\n"
            "4. 使用 ffmpeg 抽取音频、关键帧、拆分视频片段并写入对应目录。\n"
            "5. 使用 ASR 工具生成带时间戳的完整转写并写入 transcripts。\n"
            "6. 使用 PySceneDetect 或等价方式生成候选切点并写入 meta。\n"
            "7. 让大模型先浏览关键帧、理解对白、结合节奏和结构，再完成视频拆解。\n"
            "8. 按最终复杂提示词要求，把 JSON 主结果、分镜文稿、对白文稿、关键帧结论、命名清单和三套拆分视频写回工作区。\n\n"
            "结构拆解规则：\n"
            "- 不能只按视觉切点机械拆分。\n"
            "- 必须结合关键帧、场景变化、ASR 句子边界、故事动作、拍摄动作和转场。\n"
            "- 关键帧、对白、候选切点必须都参与最终结构判断，而不是孤立存在。\n"
            "- 必须输出三套拆分方案：Scheme 1 细分镜、Scheme 2 均衡分镜、Scheme 3 粗分镜。\n"
            "- 必须为三套方案分别输出对白版本。\n"
            "- 必须为三套方案分别导出拆分视频。\n"
            "- 拆分视频命名规则必须是 [序号]_[开始-结束]_[标题].mp4。\n"
            f"{formula_slot_requirement(video_formula)}"
            "输出产物要求：\n"
            "- JSON 是主结果格式，Markdown 作为补充说明。\n"
            "- 工作目录中必须至少包含：视频元数据、ASR 转写、候选切点、时间轴分析、故事公式、三套方案、关键帧、分镜、拆分视频、分析摘要。\n"
            "- 若有公式槽位映射，对应结果文件必须使用通用的 formula-slot 命名，不得在非 Hook/Trust/CTA 公式下强行输出 hook/trust/cta 文件。\n\n"
            "质量校验要求：\n"
            "在结束前必须完成质量校验，并把校验结果写入工作目录。\n"
            "必须逐项校验：\n"
            "- 视频是否真实读取成功。\n"
            "- 元数据是否已写入文件。\n"
            "- 音频、关键帧、ASR、候选切点是否均已生成。\n"
            "- 关键帧、对白、候选切点是否都已用于最终结构判断，而非孤立存在。\n"
            "- 是否明确避免了只按视觉切点机械拆分。\n"
            "- 三套拆分方案是否都已生成。\n"
            "- 三套对白版本是否都已生成。\n"
            "- 三套拆分视频是否都已导出。\n"
            "- 命名规则是否统一符合 [序号]_[开始-结束]_[标题].mp4。\n"
            "- JSON 是否为主结果格式。\n"
            "- Markdown 是否作为补充说明存在。\n"
            f"- 是否已完整校验当前视频公式“{video_formula.strip() or '当前视频公式'}”对应的组件槽位映射。\n\n"
            "最终回答要求：\n"
            "- 在聊天最终回答中，简短总结已生成的文件、核心故事公式、三套方案段数、工作目录和主要产出目录。\n"
            "- 说明本次实际调用的关键工具，以及是否完成全部质量校验。\n"
        )

    if not ctx.skill_repo.get("openflow", OPENFLOW_SKILL_KIND):
        ctx.skill_repo.upsert(
            "openflow",
            OPENFLOW_SKILL_KIND,
            DEFAULT_OPENFLOW_SKILL["title"],
            build_openflow_skill_content(),
            now_ms(),
        )

    def get_openflow_skill() -> dict[str, Any]:
        row = ctx.skill_repo.get("openflow", OPENFLOW_SKILL_KIND)
        if row:
            row["default_content"] = build_openflow_skill_content()
            return row
        return {
            "kind": OPENFLOW_SKILL_KIND,
            "title": DEFAULT_OPENFLOW_SKILL["title"],
            "content": build_openflow_skill_content(),
            "updated_at": now_ms(),
            "default_content": build_openflow_skill_content(),
        }

    def default_run_payload() -> dict[str, str]:
        return {
            "reference_video_path": "",
            "industry": "医美",
            "persona": "强判断老板型",
            "target_audience": "老板",
            "product_info": "",
            "constraints": "",
            "analysis_goal": "提取整体公式",
            "video_formula": "Hook/Trust/CTA",
            "simple_prompt": DEFAULT_SIMPLE_PROMPT,
            "final_prompt": "",
            "prompt_model_provider": "",
            "prompt_model_id": "",
            "generated_skill_content": "",
            "skill_model_provider": "",
            "skill_model_id": "",
            "skill_version_name": "",
            "skill_version_notes": "",
            "version_name": "",
            "version_notes": "",
        }

    def workspace_dirs(session_row: dict[str, Any]) -> dict[str, Path]:
        root = Path(str(session_row["workspace_dir"]))
        return {
            "workspace": root,
            "inbox": root / "inbox",
            "outbox": root / "outbox",
            "meta": root / "meta",
            "input": root / "input",
            "transcripts": root / "transcripts",
            "storyboards": root / "storyboards",
            "keyframes": root / "keyframes",
            "clips": root / "clips",
            "reports": root / "reports",
        }

    def ensure_workspace(session_row: dict[str, Any]) -> None:
        workspace_dirs(session_row)["workspace"].mkdir(parents=True, exist_ok=True)

    def clear_workspace(session_row: dict[str, Any], preserve_inbox: bool = False) -> None:
        root = Path(str(session_row["workspace_dir"]))
        if not root.exists():
            return
        for child in root.iterdir():
            if preserve_inbox and child.name == "inbox":
                continue
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)

    def reset_openflow_session(session_row: dict[str, Any], *, preserve_inbox: bool = False) -> dict[str, Any]:
        session_id = int(session_row["id"])
        clear_workspace(session_row, preserve_inbox=preserve_inbox)
        ensure_workspace(session_row)
        ctx.session_repo.delete_files_for_session(session_id)
        ctx.session_repo.delete_events_for_session(session_id)
        ctx.session_repo.update(
            session_id,
            status="queued",
            opencode_session_id=None,
            last_summary=None,
            started_at=None,
            finished_at=None,
            updated_at=now_ms(),
        )
        return safe_session(session_id)

    def resolve_reference_video_source(session_row: dict[str, Any], raw_path: str) -> Path:
        candidate = Path(raw_path).expanduser()
        if candidate.exists() and candidate.is_file():
            return candidate
        inbox = workspace_dirs(session_row)["inbox"]
        fallback = sorted([item for item in inbox.glob("reference_video*") if item.is_file()])
        if fallback:
            add_session_event(
                int(session_row["id"]),
                "tool_session.legacy_fallback",
                {
                    "reason": "reference_video_path_missing_using_inbox_fallback",
                    "path": fallback[0].relative_to(Path(str(session_row["workspace_dir"]))).as_posix(),
                    "stale_warning": "Legacy fallback is a migration-only path and may show stale artifacts.",
                },
                visibility="internal",
                event_scope="debug",
                severity="warning",
                family="tool_session",
            )
            return fallback[0]
        raise HTTPException(status_code=400, detail=f"Reference video not found: {candidate}")

    def safe_session(session_id: int) -> dict[str, Any]:
        row = ctx.session_repo.get(session_id)
        if not row:
            raise HTTPException(status_code=404, detail="Session not found")
        expected_workspace = ctx.workspace_store.sessions_root() / str(session_id) / "workspace"
        current_workspace = Path(str(row.get("workspace_dir") or ""))
        if expected_workspace.exists() and current_workspace != expected_workspace:
            ctx.session_repo.update(session_id, workspace_dir=str(expected_workspace), updated_at=now_ms())
            row["workspace_dir"] = str(expected_workspace)
        return row

    def add_session_event(session_id: int, kind: str, payload: dict[str, Any] | None, **event_fields: Any) -> int:
        if not ctx.session_repo.exists(session_id):
            return 0
        return ctx.session_event_service.add_event(session_id, kind, payload or {}, **event_fields)

    def update_session_status(session_id: int, status: str, **fields: Any) -> None:
        if not ctx.session_repo.exists(session_id):
            return
        ctx.session_repo.update(session_id, status=status, updated_at=now_ms(), **fields)

    def session_current_status(session_id: int) -> str:
        row = ctx.session_repo.get(session_id) or {}
        return str(row.get("status") or "")

    def frontend_task_url(request: Request, session_id: int) -> str:
        proto = str(request.headers.get("x-forwarded-proto") or request.url.scheme).strip() or request.url.scheme
        host = str(request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc).strip() or request.url.netloc
        prefix_raw = str(request.headers.get("x-forwarded-prefix") or "").strip()
        prefix = "" if not prefix_raw else (prefix_raw if prefix_raw.startswith("/") else f"/{prefix_raw}").rstrip("/")
        return f"{proto}://{host}{prefix}/#/sessions/task/{session_id}"

    def opencode_client_for(session_row: dict[str, Any]) -> OpenCodeSessionClient:
        base_url = str(ctx.get_setting("opencode.base_url") or "").strip()
        username = str(ctx.get_setting("opencode.username") or "").strip()
        password = str(ctx.get_setting("opencode.password") or "").strip()
        if not base_url or not username or not password:
            raise RuntimeError("OpenCode connection is incomplete. Finish Step 1 before running OpenFlow analysis.")
        return OpenCodeSessionClient(base_url=base_url, username=username, password=password, directory=str(session_row["workspace_dir"]))

    def last_completed_assistant(messages: list[dict[str, Any]], started_after: int) -> str | None:
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
        return None

    def sync_session_files(session_row: dict[str, Any]) -> list[dict[str, Any]]:
        workspace = workspace_dirs(session_row)["workspace"]
        if not workspace.exists():
            return []
        for path in workspace.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(workspace).as_posix()
            if rel == "tool_use_sessions" or rel.startswith("tool_use_sessions/"):
                continue
            stat = path.stat()
            origin = "uploaded" if rel.startswith("inbox/") else "generated"
            downloadable = 1 if not Path(rel).name.startswith(".") else 0
            ctx.session_repo.upsert_file(
                int(session_row["id"]),
                rel,
                "file",
                int(stat.st_size),
                origin,
                downloadable,
                int(stat.st_mtime * 1000),
            )
        return ctx.session_repo.list_files(int(session_row["id"]))

    def build_package_spec() -> dict[str, Any]:
        return {
            "workflow": "OpenFlow - Analysis",
            "summary_format": "json_primary",
            "export_all_scheme_videos": True,
            "dialogues_per_scheme": True,
            "schemes": [
                {"id": "scheme_1", "name": "Scheme 1", "label": "细分镜"},
                {"id": "scheme_2", "name": "Scheme 2", "label": "均衡分镜", "recommended": True},
                {"id": "scheme_3", "name": "Scheme 3", "label": "粗分镜"},
            ],
            "required_files": [
                "input/project_input.json",
                "input/simple_prompt.txt",
                "input/final_prompt.txt",
                "meta/video_metadata.json",
                "meta/asr_segments.json",
                "meta/scene_candidates.json",
                "meta/timeline_analysis.json",
                "meta/story_formula.json",
                "meta/schemes.json",
                "meta/slot_analysis.json",
                "meta/result_package_spec.json",
                "transcripts/original_asr_full.txt",
                "transcripts/original_dialogue_segments_scheme_1.json",
                "transcripts/original_dialogue_segments_scheme_2.json",
                "transcripts/original_dialogue_segments_scheme_3.json",
                "transcripts/hook_dialogue.json",
                "transcripts/trust_dialogue.json",
                "transcripts/cta_dialogue.json",
                "transcripts/rhythm_density_table.csv",
                "storyboards/scheme_1_fine_storyboard.md",
                "storyboards/scheme_2_balanced_storyboard.md",
                "storyboards/scheme_3_coarse_storyboard.md",
                "storyboards/hook_trust_cta_mapping.md",
                "storyboards/scheme_filename_manifest.json",
                "reports/analysis_summary.json",
                "reports/analysis_summary.md",
                "reports/componentized_analysis.json",
            ],
            "clip_naming_rule": "[序号]_[开始-结束]_[标题].mp4",
            "clip_examples": [
                "01_[0.0-12.7]_后台开场提出核心问题.mp4",
                "02_[12.7-24.5]_前厅边走边说客户反馈.mp4",
            ],
            "slot_schema": {
                "hook": "0-3s 的反常识冲突或强视觉抓手",
                "trust": "中段的真实证据、讲解、对比、过程展示",
                "cta": "末尾的动作引导、利益点、紧迫感或转化动作",
            },
        }

    def build_simple_prompt(project_input: dict[str, str]) -> str:
        industry = project_input["industry"] or "通用行业"
        persona = project_input["persona"] or "核心角色"
        audience = project_input["target_audience"] or "目标受众"
        product = project_input["product_info"] or "当前产品/服务"
        constraints = project_input["constraints"] or "无额外限制条件"
        goal = project_input["analysis_goal"] or "提取整体公式"
        formula = project_input["video_formula"] or "当前视频公式"
        return (
            f"按{formula}方式拆解这条视频。行业是{industry}，人设是{persona}，目标观众是{audience}。"
            f"产品/服务是{product}。分析目标是{goal}。限制条件是{constraints}。"
            f"{DEFAULT_SIMPLE_PROMPT}"
        )

    def normalize_input(payload: OpenFlowPromptGeneratePayload) -> dict[str, str]:
        return {
            "reference_video_path": payload.reference_video_path.strip(),
            "industry": payload.industry.strip(),
            "persona": payload.persona.strip(),
            "product_info": payload.product_info.strip(),
            "target_audience": payload.target_audience.strip(),
            "constraints": payload.constraints.strip(),
            "analysis_goal": payload.analysis_goal.strip(),
            "video_formula": payload.video_formula.strip(),
        }

    def normalize_prompt_model(payload: OpenFlowPromptGeneratePayload | OpenFlowVersionSavePayload) -> dict[str, str]:
        return {
            "prompt_model_provider": payload.prompt_model_provider.strip(),
            "prompt_model_id": payload.prompt_model_id.strip(),
        }

    def normalize_skill_model(payload: OpenFlowSkillBuilderPayload | OpenFlowSkillBuilderGeneratePayload) -> dict[str, str]:
        return {
            "skill_model_provider": payload.skill_model_provider.strip(),
            "skill_model_id": payload.skill_model_id.strip(),
        }

    def load_skill_builder_reference() -> str:
        return SKILL_BUILDER_REFERENCE.read_text(encoding="utf-8").strip()

    def build_prompt_builder_input(project_input: dict[str, str], simple_prompt: str) -> str:
        fields = [
            ("Industry", project_input["industry"] or "通用行业"),
            ("Persona", project_input["persona"] or "核心角色"),
            ("Target Audience", project_input["target_audience"] or "目标受众"),
            ("Product Info", project_input["product_info"] or "未提供"),
            ("Constraints", project_input["constraints"] or "无额外限制条件"),
            ("Analysis Goal", project_input["analysis_goal"] or "提取整体公式"),
            ("Video Formula", project_input["video_formula"] or "当前视频公式"),
            ("Simple Prompt", simple_prompt.strip() or DEFAULT_SIMPLE_PROMPT),
        ]
        return "\n".join([f"{label}: {value}" for label, value in fields])

    def serialize_prompt_models(session_row: dict[str, Any]) -> dict[str, Any]:
        client = opencode_client_for(session_row)
        provider_payload = client.providers()
        connected = set([str(item) for item in (provider_payload.get("connected") or []) if item])
        default_map = provider_payload.get("default") or {}
        items: list[dict[str, Any]] = []
        default_model = {"providerID": "", "modelID": ""}
        for provider in provider_payload.get("all") or []:
            provider_id = str(provider.get("id") or "").strip()
            if not provider_id or provider_id not in connected:
                continue
            provider_name = str(provider.get("name") or provider_id)
            models = provider.get("models") or {}
            for model in models.values():
                model_id = str((model or {}).get("id") or "").strip()
                if not model_id:
                    continue
                items.append({
                    "providerID": provider_id,
                    "providerName": provider_name,
                    "modelID": model_id,
                    "modelName": str((model or {}).get("name") or model_id),
                    "reasoning": bool((model or {}).get("reasoning")),
                    "contextLimit": int((((model or {}).get("limit") or {}).get("context") or 0) or 0),
                    "inputModalities": list((((model or {}).get("modalities") or {}).get("input") or [])),
                })
            configured_default = str(default_map.get(provider_id) or "").strip()
            if configured_default and not default_model["providerID"]:
                default_model = {"providerID": provider_id, "modelID": configured_default}
        items.sort(key=lambda item: (str(item["providerName"]), str(item["modelName"])))
        if not default_model["providerID"] and items:
            default_model = {"providerID": str(items[0]["providerID"]), "modelID": str(items[0]["modelID"])}
        return {"items": items, "default_model": default_model}

    def latest_saved_final_prompt_version(run_row: dict[str, Any]) -> dict[str, Any]:
        versions = serialize_versions(str(run_row.get("versions_json") or "[]"))
        for version in versions:
            final_prompt = str(version.get("final_prompt") or "").strip()
            if not final_prompt:
                continue
            return version
        raise HTTPException(status_code=400, detail="Skill Builder requires a saved Final Prompt version before generating. Save a Prompt Builder version first.")

    def latest_saved_skill_version(run_row: dict[str, Any]) -> dict[str, Any]:
        versions = serialize_versions(str(run_row.get("skill_versions_json") or "[]"))
        for version in versions:
            content = str(version.get("content") or "").strip()
            if not content:
                continue
            return version
        raise HTTPException(status_code=400, detail="Run requires a saved Skill version before execution. Save a Skill Builder version first.")

    def resolve_prompt_model(session_row: dict[str, Any], payload: OpenFlowPromptGeneratePayload | OpenFlowVersionSavePayload, role: str = "admin") -> tuple[dict[str, str], dict[str, Any]]:
        prompt_models = serialize_prompt_models(session_row)
        requested = normalize_prompt_model(payload)
        model, masked_prompt_models = resolve_prompt_model_for_role(
            ctx,
            role,
            SURFACE_OPENFLOW_PROMPT,
            prompt_models,
            requested["prompt_model_provider"],
            requested["prompt_model_id"],
            "Prompt",
        )
        return {"prompt_model_provider": model["providerID"], "prompt_model_id": model["modelID"]}, masked_prompt_models

    def resolve_skill_model(session_row: dict[str, Any], payload: OpenFlowSkillBuilderPayload | OpenFlowSkillBuilderGeneratePayload, role: str = "admin") -> tuple[dict[str, str], dict[str, Any]]:
        prompt_models = serialize_prompt_models(session_row)
        requested = normalize_skill_model(payload)
        model, masked_prompt_models = resolve_prompt_model_for_role(
            ctx,
            role,
            SURFACE_OPENFLOW_PROMPT,
            prompt_models,
            requested["skill_model_provider"],
            requested["skill_model_id"],
            "Skill",
        )
        return {"skill_model_provider": model["providerID"], "skill_model_id": model["modelID"]}, masked_prompt_models

    def validate_final_prompt_text(final_prompt: str) -> None:
        lowered = final_prompt.lower()
        matched = [keyword for keyword in FINAL_PROMPT_TECH_KEYWORDS if keyword.lower() in lowered]
        if matched:
            raise RuntimeError(f"Generated final prompt still contains technical content: {', '.join(matched[:5])}")

    def generate_final_prompt_with_model(
        session_row: dict[str, Any],
        project_input: dict[str, str],
        simple_prompt: str,
        prompt_model: dict[str, str],
    ) -> str:
        client = opencode_client_for(session_row)
        prompt_input = build_prompt_builder_input(project_input, simple_prompt)
        last_error: Exception | None = None
        for _ in range(2):
            temp_session = client.create_session(f"{session_row['title']} Prompt Builder")
            started_at = now_ms()
            client.prompt_async(
                str(temp_session["id"]),
                prompt_input,
                model={"providerID": prompt_model["prompt_model_provider"], "modelID": prompt_model["prompt_model_id"]},
                system=PROMPT_BUILDER_SYSTEM_PROMPT,
            )
            deadline = time.time() + 180
            assistant_text: str | None = None
            while time.time() < deadline:
                messages = client.messages(str(temp_session["id"]), limit=40)
                assistant_text = last_completed_assistant(messages, started_at)
                if assistant_text:
                    break
                time.sleep(1)
            if not assistant_text:
                last_error = RuntimeError("OpenCode timed out before returning the generated final prompt")
                continue
            try:
                validate_final_prompt_text(assistant_text)
                return assistant_text.strip()
            except Exception as exc:
                last_error = exc
        raise HTTPException(status_code=400, detail=str(last_error or RuntimeError("Failed to generate final prompt")))

    def build_skill_builder_input(session_row: dict[str, Any], run_row: dict[str, Any]) -> str:
        version = latest_saved_final_prompt_version(run_row)
        video_formula = str(run_row.get("video_formula") or "")
        project_input = {
            "reference_video_path": str(run_row.get("reference_video_path") or ""),
            "industry": str(run_row.get("industry") or ""),
            "persona": str(run_row.get("persona") or ""),
            "target_audience": str(run_row.get("target_audience") or ""),
            "product_info": str(run_row.get("product_info") or ""),
            "constraints": str(run_row.get("constraints") or ""),
            "analysis_goal": str(run_row.get("analysis_goal") or ""),
            "video_formula": str(run_row.get("video_formula") or ""),
        }
        lines = [
            f"Session Title: {session_row['title']}",
            f"Latest Saved Final Prompt Version ID: {version.get('id')}",
            f"Latest Saved Final Prompt Version Name: {version.get('name') or version.get('id')}",
            "",
            "Project Input:",
            *[f"- {key}: {value or '未提供'}" for key, value in project_input.items()],
            "",
            "Latest Saved Final Prompt:",
            str(version.get("final_prompt") or "").strip(),
            "",
            "Current Technical Baseline:",
            build_openflow_skill_content(video_formula).strip(),
            "",
            "Reference Workflow:",
            load_skill_builder_reference(),
        ]
        return "\n".join(lines).strip()

    def generate_skill_with_model(session_row: dict[str, Any], run_row: dict[str, Any], skill_model: dict[str, str]) -> str:
        client = opencode_client_for(session_row)
        prompt_input = build_skill_builder_input(session_row, run_row)
        temp_session = client.create_session(f"{session_row['title']} Skill Builder")
        started_at = now_ms()
        client.prompt_async(
            str(temp_session["id"]),
            prompt_input,
            model={"providerID": skill_model["skill_model_provider"], "modelID": skill_model["skill_model_id"]},
            system=SKILL_BUILDER_SYSTEM_PROMPT,
        )
        deadline = time.time() + 240
        assistant_text: str | None = None
        while time.time() < deadline:
            messages = client.messages(str(temp_session["id"]), limit=40)
            assistant_text = last_completed_assistant(messages, started_at)
            if assistant_text:
                return assistant_text.strip()
            time.sleep(1)
        raise HTTPException(status_code=400, detail="OpenCode timed out before returning the generated skill")

    def serialize_skill_builder_payload(session_row: dict[str, Any], run_row: dict[str, Any]) -> dict[str, Any]:
        latest_prompt_version = None
        try:
            version = latest_saved_final_prompt_version(run_row)
            latest_prompt_version = {
                "id": str(version.get("id") or ""),
                "name": str(version.get("name") or ""),
                "updated_at": int(version.get("updated_at") or 0),
            }
        except HTTPException:
            latest_prompt_version = None
        return {
            "session_id": int(session_row["id"]),
            "generated_skill_content": str(run_row.get("generated_skill_content") or get_openflow_skill()["content"]),
            "skill_model_provider": str(run_row.get("skill_model_provider") or ""),
            "skill_model_id": str(run_row.get("skill_model_id") or ""),
            "skill_version_name": str(run_row.get("skill_version_name") or ""),
            "skill_version_notes": str(run_row.get("skill_version_notes") or ""),
            "versions": serialize_versions(str(run_row.get("skill_versions_json") or "[]")),
            "latest_prompt_version": latest_prompt_version,
        }

    def mask_for_role(role: str, payload: Any) -> Any:
        return mask_model_fields_for_role(ctx, role, SURFACE_OPENFLOW_PROMPT, payload)

    def mask_prompt_models(role: str, prompt_models: dict[str, Any]) -> dict[str, Any]:
        return mask_prompt_models_for_role(ctx, role, SURFACE_OPENFLOW_PROMPT, prompt_models)

    def create_session_record(command_text: str) -> dict[str, Any]:
        title = ctx.next_session_title()
        share_token = ctx.new_share_token()
        created = now_ms()
        session_id = ctx.session_repo.create(
            source=OPENFLOW_SOURCE,
            group_id=OPENFLOW_GROUP_ID,
            sender_name="OpenFlow",
            title=title,
            command_text=command_text,
            status="queued",
            workspace_dir=str(ctx.workspace_store.sessions_root() / "pending" / str(created) / "workspace"),
            share_token=share_token,
            created_at=created,
            updated_at=created,
        )
        workspace_dir = ctx.workspace_store.create_session_workspace(session_id)
        ctx.session_repo.update(session_id, workspace_dir=str(workspace_dir), updated_at=created)
        add_session_event(session_id, "session.created", {"title": title, "group_id": OPENFLOW_GROUP_ID, "sender_name": "OpenFlow"})
        return safe_session(session_id)

    def ensure_openflow_run(session_id: int) -> dict[str, Any]:
        row = ctx.openflow_repo.get_by_session(session_id)
        if row:
            return row
        created = now_ms()
        ctx.openflow_repo.create_for_session(session_id, created, **default_run_payload())
        return ctx.openflow_repo.get_by_session(session_id) or {}

    def serialize_versions(raw: str | None) -> list[dict[str, Any]]:
        try:
            parsed = json.loads(raw or "[]")
            if isinstance(parsed, list):
                return [item for item in parsed if isinstance(item, dict)]
        except Exception:
            return []
        return []

    def serialize_run_payload(session_row: dict[str, Any], run_row: dict[str, Any]) -> dict[str, Any]:
        payload = default_run_payload()
        payload.update({
            "reference_video_path": str(run_row.get("reference_video_path") or ""),
            "industry": str(run_row.get("industry") or payload["industry"]),
            "persona": str(run_row.get("persona") or payload["persona"]),
            "target_audience": str(run_row.get("target_audience") or payload["target_audience"]),
            "product_info": str(run_row.get("product_info") or ""),
            "constraints": str(run_row.get("constraints") or ""),
            "analysis_goal": str(run_row.get("analysis_goal") or payload["analysis_goal"]),
            "video_formula": str(run_row.get("video_formula") or payload["video_formula"]),
            "simple_prompt": str(run_row.get("simple_prompt") or payload["simple_prompt"]),
            "final_prompt": str(run_row.get("final_prompt") or ""),
            "prompt_model_provider": str(run_row.get("prompt_model_provider") or ""),
            "prompt_model_id": str(run_row.get("prompt_model_id") or ""),
            "generated_skill_content": str(run_row.get("generated_skill_content") or ""),
            "skill_model_provider": str(run_row.get("skill_model_provider") or ""),
            "skill_model_id": str(run_row.get("skill_model_id") or ""),
            "skill_version_name": str(run_row.get("skill_version_name") or ""),
            "skill_version_notes": str(run_row.get("skill_version_notes") or ""),
            "version_name": str(run_row.get("version_name") or ""),
            "version_notes": str(run_row.get("version_notes") or ""),
        })
        return {
            "session_id": int(session_row["id"]),
            "session_title": str(session_row["title"]),
            "session_status": str(session_row.get("status") or "draft"),
            **payload,
            "versions": serialize_versions(str(run_row.get("versions_json") or "[]")),
        }

    def create_draft_session() -> dict[str, Any]:
        row = create_session_record("")
        created = now_ms()
        ctx.session_repo.update(int(row["id"]), status="draft", updated_at=created)
        ctx.openflow_repo.create_for_session(int(row["id"]), created, **default_run_payload())
        ctx.set_setting("openflow.analysis.active_session_id", int(row["id"]))
        add_session_event(int(row["id"]), "openflow.analysis.draft.created", {"session_id": int(row["id"])})
        return safe_session(int(row["id"]))

    def current_draft_session() -> dict[str, Any]:
        current_id = int(ctx.get_setting("openflow.analysis.active_session_id") or 0)
        row = ctx.session_repo.get(current_id) if current_id else None
        if row and row.get("source") == OPENFLOW_SOURCE and str(row.get("status") or "") != "cancelled":
            ensure_openflow_run(int(row["id"]))
            return row
        return create_draft_session()

    def write_text(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def write_json(path: Path, payload: dict[str, Any]) -> None:
        write_text(path, json.dumps(payload, ensure_ascii=False, indent=2))

    def stage_reference_video(session_row: dict[str, Any], project_input: dict[str, str]) -> dict[str, Any]:
        source = resolve_reference_video_source(session_row, project_input["reference_video_path"])
        ensure_workspace(session_row)
        suffix = source.suffix or ".mp4"
        target = workspace_dirs(session_row)["inbox"] / f"reference_video{suffix}"
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.resolve() != target.resolve():
            shutil.copy2(source, target)
        add_session_event(int(session_row["id"]), "openflow.reference_video.staged", {"source": str(source), "target": str(target)})
        return {"source": str(source), "target": str(target)}

    def stage_openflow_inputs(session_row: dict[str, Any], project_input: dict[str, str], simple_prompt: str, final_prompt: str, skill_content: str, skill_version: dict[str, Any]) -> dict[str, Any]:
        ensure_workspace(session_row)
        dirs = workspace_dirs(session_row)
        package_spec = build_package_spec()
        staged_video = stage_reference_video(session_row, project_input)
        staged_project_input = dict(project_input)
        staged_project_input["reference_video_path"] = str(staged_video["target"])
        write_json(dirs["input"] / "project_input.json", staged_project_input)
        write_text(dirs["input"] / "simple_prompt.txt", simple_prompt)
        write_text(dirs["input"] / "final_prompt.txt", final_prompt)
        write_text(dirs["input"] / "skill.txt", skill_content)
        write_json(dirs["meta"] / "result_package_spec.json", package_spec)
        write_json(dirs["meta"] / "run_manifest.json", {
            "workflow": "OpenFlow - Analysis",
            "staged_video": staged_video,
            "skill_version": {
                "id": str(skill_version.get("id") or ""),
                "name": str(skill_version.get("name") or ""),
                "notes": str(skill_version.get("notes") or ""),
            },
            "created_at": now_ms(),
        })
        write_text(dirs["reports"] / "analysis_summary.md", "# OpenFlow Analysis\n\nPending execution.\n")
        return package_spec

    def stream_prompt(session_id: int, prompt: str, system_prompt: str | None = None) -> None:
        session_row = safe_session(session_id)
        prompt_started_at = now_ms()
        update_session_status(session_id, "running", started_at=prompt_started_at)
        add_session_event(session_id, "user.message", {"text": prompt})
        stop_event = threading.Event()

        def on_event(payload: dict[str, Any]) -> None:
            if session_current_status(session_id) == "cancelled":
                return
            raw_kind = str(payload.get("type") or "event").strip() or "event"
            kind = raw_kind if raw_kind.startswith("opencode.") else f"opencode.{raw_kind}"
            raw_properties = payload.get("properties") or {}
            properties = raw_properties if isinstance(raw_properties, dict) else {}
            add_session_event(
                session_id,
                kind,
                properties if isinstance(raw_properties, dict) else {"value": raw_properties},
                visibility="internal",
                event_scope="debug",
                family="opencode",
            )
            if raw_kind == "session.idle":
                update_session_status(session_id, "waiting_input")
            elif raw_kind == "session.status":
                status = str(((properties.get("status") or {}).get("type") or "running")).strip() or "running"
                update_session_status(session_id, status)

        try:
            client = opencode_client_for(session_row)
            collector = threading.Thread(
                target=client.collect_events,
                args=(str(session_row["opencode_session_id"]), stop_event, on_event),
                daemon=True,
            )
            collector.start()
            client.prompt_async(str(session_row["opencode_session_id"]), prompt, system=system_prompt)
            deadline = time.time() + 1800
            assistant_text: str | None = None
            while time.time() < deadline:
                if session_current_status(session_id) == "cancelled":
                    return
                messages = client.messages(str(session_row["opencode_session_id"]), limit=80)
                assistant_text = last_completed_assistant(messages, prompt_started_at)
                if assistant_text:
                    break
                time.sleep(1)
            if not assistant_text:
                raise RuntimeError("OpenCode timed out before returning an assistant response")
            if session_current_status(session_id) == "cancelled":
                return
            add_session_event(session_id, "assistant.final", {"text": assistant_text})
            update_session_status(session_id, "waiting_input", last_summary=assistant_text[:4000], finished_at=now_ms())
            sync_session_files(safe_session(session_id))
        except Exception as exc:
            if session_current_status(session_id) == "cancelled":
                return
            add_session_event(session_id, "session.error", {"message": str(exc)})
            update_session_status(session_id, "failed", finished_at=now_ms())
        finally:
            stop_event.set()

    def start_prompt_thread(session_id: int, prompt: str, system_prompt: str | None = None) -> None:
        thread = threading.Thread(target=stream_prompt, args=(session_id, prompt, system_prompt), daemon=True)
        thread.start()

    def bind_openflow_session(request: Request, session_row: dict[str, Any], command_text: str) -> dict[str, Any]:
        ctx.set_setting("openflow.analysis.active_session_id", int(session_row["id"]))
        ctx.session_repo.update(int(session_row["id"]), command_text=command_text, updated_at=now_ms())
        try:
            client = opencode_client_for(session_row)
            op_session = client.create_session(str(session_row["title"]))
            ctx.session_repo.update(
                int(session_row["id"]),
                opencode_session_id=str(op_session["id"]),
                status="running",
                started_at=now_ms(),
                updated_at=now_ms(),
            )
            add_session_event(int(session_row["id"]), "session.bound", {"opencode_session_id": str(op_session["id"])})
            return {
                "session": safe_session(int(session_row["id"])),
                "task_url": frontend_task_url(request, int(session_row["id"])),
            }
        except Exception as exc:
            add_session_event(int(session_row["id"]), "session.error", {"message": str(exc)})
            update_session_status(int(session_row["id"]), "failed", finished_at=now_ms())
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/api/openflow/analysis/config")
    async def openflow_analysis_config(request: Request, session_id: int = Query(default=0)) -> dict[str, Any]:
        role = request_role(request)
        session_row = safe_session(session_id) if session_id else current_draft_session()
        ctx.set_setting("openflow.analysis.active_session_id", int(session_row["id"]))
        run_row = ensure_openflow_run(int(session_row["id"]))
        try:
            prompt_models = mask_prompt_models(role, serialize_prompt_models(session_row))
        except Exception as exc:
            prompt_models = {"items": [], "default_model": {"providerID": "", "modelID": ""}, "error": str(exc)}
        return mask_for_role(role, {
            "default_simple_prompt": DEFAULT_SIMPLE_PROMPT,
            "skill": get_openflow_skill(),
            "draft": serialize_run_payload(session_row, run_row),
            "package_spec": build_package_spec(),
            "prompt_models": prompt_models,
            "skill_builder": serialize_skill_builder_payload(session_row, run_row),
            "options": {
                "industry": INDUSTRY_OPTIONS,
                "persona": PERSONA_OPTIONS,
                "target_audience": TARGET_AUDIENCE_OPTIONS,
                "analysis_goal": ANALYSIS_GOAL_OPTIONS,
                "video_formula": VIDEO_FORMULA_OPTIONS,
            },
        })

    @router.post("/api/openflow/analysis/session/new")
    async def openflow_analysis_new_session(request: Request) -> dict[str, Any]:
        role = request_role(request)
        session_row = create_draft_session()
        run_row = ensure_openflow_run(int(session_row["id"]))
        try:
            prompt_models = mask_prompt_models(role, serialize_prompt_models(session_row))
        except Exception as exc:
            prompt_models = {"items": [], "default_model": {"providerID": "", "modelID": ""}, "error": str(exc)}
        return mask_for_role(role, {
            "ok": True,
            "draft": serialize_run_payload(session_row, run_row),
            "skill_builder": serialize_skill_builder_payload(session_row, run_row),
            "skill": get_openflow_skill(),
            "package_spec": build_package_spec(),
            "prompt_models": prompt_models,
            "options": {
                "industry": INDUSTRY_OPTIONS,
                "persona": PERSONA_OPTIONS,
                "target_audience": TARGET_AUDIENCE_OPTIONS,
                "analysis_goal": ANALYSIS_GOAL_OPTIONS,
                "video_formula": VIDEO_FORMULA_OPTIONS,
            },
        })

    @router.get("/api/openflow/analysis/prompt-models")
    async def openflow_analysis_prompt_models(request: Request) -> dict[str, Any]:
        session_row = current_draft_session()
        return mask_prompt_models(request_role(request), serialize_prompt_models(session_row))

    @router.put("/api/openflow/analysis/config")
    async def openflow_analysis_save_config(request: Request, payload: OpenFlowPromptGeneratePayload) -> dict[str, Any]:
        role = request_role(request)
        session_id = int(payload.session_id or 0)
        session_row = safe_session(session_id) if session_id else current_draft_session()
        project_input = normalize_input(payload)
        simple_prompt = payload.simple_prompt.strip() or build_simple_prompt(project_input)
        prompt_model = normalize_prompt_model(payload)
        if role != "admin":
            prompt_model, _ = resolve_prompt_model(session_row, payload, role)
        updated = now_ms()
        ctx.openflow_repo.upsert_for_session(
            int(session_row["id"]),
            updated,
            **project_input,
            **prompt_model,
            simple_prompt=simple_prompt,
            final_prompt=payload.final_prompt.strip(),
            version_notes=payload.version_notes.strip(),
        )
        add_session_event(int(session_row["id"]), "openflow.analysis.config.saved", {"session_id": int(session_row["id"])})
        return mask_for_role(role, {
            "ok": True,
            "draft": serialize_run_payload(safe_session(int(session_row["id"])), ensure_openflow_run(int(session_row["id"]))),
            "simple_prompt": simple_prompt,
        })

    @router.post("/api/openflow/analysis/simple-prompt/rebuild")
    async def openflow_analysis_rebuild_simple_prompt(payload: OpenFlowPromptGeneratePayload) -> dict[str, Any]:
        project_input = normalize_input(payload)
        return {"ok": True, "simple_prompt": build_simple_prompt(project_input)}

    @router.post("/api/openflow/analysis/versions")
    async def openflow_analysis_save_version(request: Request, payload: OpenFlowVersionSavePayload) -> dict[str, Any]:
        role = request_role(request)
        session_row = safe_session(payload.session_id)
        run_row = ensure_openflow_run(int(session_row["id"]))
        project_input = {
            "reference_video_path": payload.reference_video_path.strip(),
            "industry": payload.industry.strip(),
            "persona": payload.persona.strip(),
            "target_audience": payload.target_audience.strip(),
            "product_info": payload.product_info.strip(),
            "constraints": payload.constraints.strip(),
            "analysis_goal": payload.analysis_goal.strip(),
            "video_formula": payload.video_formula.strip(),
        }
        versions = serialize_versions(str(run_row.get("versions_json") or "[]"))
        version_id = f"v{now_ms()}"
        version_name = payload.version_name.strip() or version_id
        prompt_model = {
            "prompt_model_provider": payload.prompt_model_provider.strip(),
            "prompt_model_id": payload.prompt_model_id.strip(),
        }
        if role != "admin":
            prompt_model, _ = resolve_prompt_model(session_row, payload, role)
        versions.insert(0, {
            "id": version_id,
            "name": version_name,
            "notes": payload.version_notes.strip(),
            "simple_prompt": payload.simple_prompt.strip() or build_simple_prompt(project_input),
            "final_prompt": payload.final_prompt.strip(),
            "prompt_model_provider": prompt_model["prompt_model_provider"],
            "prompt_model_id": prompt_model["prompt_model_id"],
            "input": project_input,
            "updated_at": now_ms(),
        })
        ctx.openflow_repo.update_for_session(int(session_row["id"]), versions_json=json.dumps(versions, ensure_ascii=False), version_name=version_name, version_notes=payload.version_notes.strip(), updated_at=now_ms())
        return mask_for_role(role, {"ok": True, "versions": versions})

    @router.post("/api/openflow/analysis/versions/load")
    async def openflow_analysis_load_version(request: Request, payload: OpenFlowVersionLoadPayload) -> dict[str, Any]:
        role = request_role(request)
        session_row = safe_session(payload.session_id)
        run_row = ensure_openflow_run(int(session_row["id"]))
        versions = serialize_versions(str(run_row.get("versions_json") or "[]"))
        version = next((item for item in versions if str(item.get("id")) == payload.version_id), None)
        if not version:
            raise HTTPException(status_code=404, detail="Version not found")
        version_input = version.get("input") or {}
        ctx.openflow_repo.update_for_session(
            int(session_row["id"]),
            **{key: str(version_input.get(key) or "") for key in ["reference_video_path", "industry", "persona", "target_audience", "product_info", "constraints", "analysis_goal", "video_formula"]},
            simple_prompt=str(version.get("simple_prompt") or ""),
            final_prompt=str(version.get("final_prompt") or ""),
            prompt_model_provider=str(version.get("prompt_model_provider") or ""),
            prompt_model_id=str(version.get("prompt_model_id") or ""),
            version_name=str(version.get("name") or ""),
            version_notes=str(version.get("notes") or ""),
            updated_at=now_ms(),
        )
        return mask_for_role(role, {"ok": True, "draft": serialize_run_payload(safe_session(int(session_row["id"])), ensure_openflow_run(int(session_row["id"])))})

    @router.post("/api/openflow/analysis/versions/update")
    async def openflow_analysis_update_version(request: Request, payload: OpenFlowVersionLoadPayload) -> dict[str, Any]:
        role = request_role(request)
        session_row = safe_session(payload.session_id)
        run_row = ensure_openflow_run(int(session_row["id"]))
        versions = serialize_versions(str(run_row.get("versions_json") or "[]"))
        version = next((item for item in versions if str(item.get("id")) == payload.version_id), None)
        if not version:
            raise HTTPException(status_code=404, detail="Version not found")
        project_input = {key: str(run_row.get(key) or "") for key in ["reference_video_path", "industry", "persona", "target_audience", "product_info", "constraints", "analysis_goal", "video_formula"]}
        version.update({
            "notes": str(run_row.get("version_notes") or ""),
            "simple_prompt": str(run_row.get("simple_prompt") or "") or build_simple_prompt(project_input),
            "final_prompt": str(run_row.get("final_prompt") or ""),
            "prompt_model_provider": str(run_row.get("prompt_model_provider") or ""),
            "prompt_model_id": str(run_row.get("prompt_model_id") or ""),
            "input": project_input,
        })
        ctx.openflow_repo.update_for_session(int(session_row["id"]), versions_json=json.dumps(versions, ensure_ascii=False), updated_at=now_ms())
        return mask_for_role(role, {"ok": True, "draft": serialize_run_payload(safe_session(int(session_row["id"])), ensure_openflow_run(int(session_row["id"])))})

    @router.post("/api/openflow/analysis/versions/delete")
    async def openflow_analysis_delete_version(request: Request, payload: OpenFlowVersionDeletePayload) -> dict[str, Any]:
        role = request_role(request)
        session_row = safe_session(payload.session_id)
        run_row = ensure_openflow_run(int(session_row["id"]))
        versions = serialize_versions(str(run_row.get("versions_json") or "[]"))
        filtered = [item for item in versions if str(item.get("id")) != payload.version_id]
        if len(filtered) == len(versions):
            raise HTTPException(status_code=404, detail="Version not found")
        ctx.openflow_repo.update_for_session(int(session_row["id"]), versions_json=json.dumps(filtered, ensure_ascii=False), updated_at=now_ms())
        return mask_for_role(role, {"ok": True, "versions": filtered})

    @router.post("/api/openflow/analysis/generate-prompt")
    async def openflow_analysis_generate_prompt(request: Request, payload: OpenFlowPromptGeneratePayload) -> dict[str, Any]:
        role = request_role(request)
        session_id = int(payload.session_id or 0)
        session_row = safe_session(session_id) if session_id else current_draft_session()
        project_input = normalize_input(payload)
        simple_prompt = payload.simple_prompt.strip() or build_simple_prompt(project_input)
        skill = get_openflow_skill()
        prompt_model, prompt_models = resolve_prompt_model(session_row, payload, role)
        final_prompt = generate_final_prompt_with_model(session_row, project_input, simple_prompt, prompt_model)
        ctx.openflow_repo.upsert_for_session(
            int(session_row["id"]),
            now_ms(),
            **project_input,
            **prompt_model,
            simple_prompt=simple_prompt,
            final_prompt=final_prompt,
            version_notes=payload.version_notes.strip(),
        )
        add_session_event(
            int(session_row["id"]),
            "openflow.analysis.prompt.generated",
            {
                "provider": prompt_model["prompt_model_provider"],
                "model": prompt_model["prompt_model_id"],
            },
        )
        return mask_for_role(role, {
            "ok": True,
            "draft": serialize_run_payload(safe_session(int(session_row["id"])), ensure_openflow_run(int(session_row["id"]))),
            "simple_prompt": simple_prompt,
            "final_prompt": final_prompt,
            "package_spec": build_package_spec(),
            "skill": skill,
            "prompt_models": prompt_models,
            "skill_builder": serialize_skill_builder_payload(safe_session(int(session_row["id"])), ensure_openflow_run(int(session_row["id"]))),
            "used_prompt_model_provider": prompt_model["prompt_model_provider"],
            "used_prompt_model_id": prompt_model["prompt_model_id"],
        })

    @router.post("/api/openflow/analysis/run")
    async def openflow_analysis_run(request: Request, payload: OpenFlowAnalysisRunPayload) -> dict[str, Any]:
        session_id = int(payload.session_id or 0)
        if not session_id:
            raise HTTPException(status_code=400, detail="OpenFlow draft session is required")
        session_row = safe_session(session_id)
        run_row = ensure_openflow_run(int(session_row["id"]))
        project_input = normalize_input(payload)
        if not project_input["reference_video_path"]:
            raise HTTPException(status_code=400, detail="Reference video path is required before running analysis")
        if not project_input["persona"]:
            raise HTTPException(status_code=400, detail="Persona is required before running analysis")
        if not project_input["analysis_goal"]:
            raise HTTPException(status_code=400, detail="Analysis goal is required before running analysis")
        if not project_input["video_formula"]:
            raise HTTPException(status_code=400, detail="Video formula is required before running analysis")
        simple_prompt = payload.simple_prompt.strip() or build_simple_prompt(project_input)
        final_prompt = payload.final_prompt.strip()
        if not final_prompt:
            raise HTTPException(status_code=400, detail="Final prompt is required")
        skill_version = latest_saved_skill_version(run_row)
        skill_content = str(skill_version.get("content") or "").strip()
        if not skill_content:
            raise HTTPException(status_code=400, detail="Latest saved Skill version is empty")
        if session_row.get("opencode_session_id") or str(session_row.get("status") or "") not in {"draft", "queued"}:
            session_row = reset_openflow_session(session_row, preserve_inbox=True)
            add_session_event(int(session_row["id"]), "session.rerun", {"source": OPENFLOW_SOURCE})
        if not session_row.get("opencode_session_id"):
            created = bind_openflow_session(request, session_row, simple_prompt)
            session_row = created["session"]
        else:
            created = {"session": session_row, "task_url": frontend_task_url(request, int(session_row["id"]))}
        package_spec = stage_openflow_inputs(session_row, project_input, simple_prompt, final_prompt, skill_content, skill_version)
        sync_session_files(session_row)
        ctx.openflow_repo.upsert_for_session(
            int(session_row["id"]),
            now_ms(),
            **project_input,
            simple_prompt=simple_prompt,
            final_prompt=final_prompt,
            generated_skill_content=skill_content,
            skill_model_provider=str(skill_version.get("skill_model_provider") or run_row.get("skill_model_provider") or ""),
            skill_model_id=str(skill_version.get("skill_model_id") or run_row.get("skill_model_id") or ""),
            skill_version_name=str(skill_version.get("name") or ""),
            skill_version_notes=str(skill_version.get("notes") or ""),
        )
        add_session_event(int(session_row["id"]), "openflow.analysis.started", {
            "package_spec": package_spec,
            "skill_version": {
                "id": str(skill_version.get("id") or ""),
                "name": str(skill_version.get("name") or ""),
            },
        })
        start_prompt_thread(int(session_row["id"]), final_prompt, skill_content)
        return {
            "ok": True,
            "session_id": int(session_row["id"]),
            "task_url": created["task_url"],
            "package_spec": package_spec,
            "used_skill_version_id": str(skill_version.get("id") or ""),
            "used_skill_version_name": str(skill_version.get("name") or ""),
        }

    @router.post("/api/openflow/analysis/rerun")
    async def openflow_analysis_rerun(request: Request, session_id: int = Query(...)) -> dict[str, Any]:
        session_row = safe_session(session_id)
        if str(session_row.get("source") or "") != OPENFLOW_SOURCE:
            raise HTTPException(status_code=400, detail="Not an OpenFlow session")
        run_row = ensure_openflow_run(session_id)
        project_input = {
            "reference_video_path": str(run_row.get("reference_video_path") or "").strip(),
            "industry": str(run_row.get("industry") or "").strip(),
            "persona": str(run_row.get("persona") or "").strip(),
            "target_audience": str(run_row.get("target_audience") or "").strip(),
            "product_info": str(run_row.get("product_info") or "").strip(),
            "constraints": str(run_row.get("constraints") or "").strip(),
            "analysis_goal": str(run_row.get("analysis_goal") or "").strip(),
            "video_formula": str(run_row.get("video_formula") or "").strip(),
        }
        final_prompt = str(run_row.get("final_prompt") or "").strip()
        if not final_prompt:
            raise HTTPException(status_code=400, detail="Final prompt is required before rerunning this session")
        skill_version = latest_saved_skill_version(run_row)
        skill_content = str(skill_version.get("content") or "").strip()
        if not skill_content:
            raise HTTPException(status_code=400, detail="Latest saved Skill version is empty")
        simple_prompt = str(run_row.get("simple_prompt") or build_simple_prompt(project_input)).strip()
        # Claim the session atomically (after the cheap read-only validations above)
        # so two concurrent reruns cannot both reset the workspace and clobber each
        # other. Only the racer that flips the status here proceeds; others get 409.
        if not ctx.session_repo.claim_for_rerun(
            session_id,
            busy_statuses=("running", "queued", "rerun_preparing"),
            claim_status="rerun_preparing",
            updated_at=now_ms(),
        ):
            raise HTTPException(status_code=409, detail="Stop the active run before rerunning this session")
        # Everything below runs under the claimed sentinel; any failure must clear it
        # (to "failed") so the session is not left stuck busy.
        try:
            session_row = reset_openflow_session(session_row, preserve_inbox=True)
            add_session_event(session_id, "session.rerun", {"source": OPENFLOW_SOURCE})
            created = bind_openflow_session(request, session_row, simple_prompt)
            session_row = created["session"]
            package_spec = stage_openflow_inputs(session_row, project_input, simple_prompt, final_prompt, skill_content, skill_version)
            sync_session_files(session_row)
            add_session_event(session_id, "openflow.analysis.started", {
                "package_spec": package_spec,
                "skill_version": {
                    "id": str(skill_version.get("id") or ""),
                    "name": str(skill_version.get("name") or ""),
                },
                "rerun": True,
            })
            start_prompt_thread(session_id, final_prompt, skill_content)
        except HTTPException:
            update_session_status(session_id, "failed", finished_at=now_ms())
            raise
        except Exception as exc:
            update_session_status(session_id, "failed", finished_at=now_ms())
            add_session_event(session_id, "session.error", {"message": str(exc)})
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "session_id": session_id, "task_url": created["task_url"], "package_spec": package_spec}

    @router.get("/api/openflow/analysis/skill")
    async def openflow_analysis_skill() -> dict[str, Any]:
        return get_openflow_skill()

    @router.get("/api/openflow/analysis/skill-builder")
    async def openflow_analysis_skill_builder(request: Request) -> dict[str, Any]:
        role = request_role(request)
        session_row = current_draft_session()
        run_row = ensure_openflow_run(int(session_row["id"]))
        try:
            prompt_models = mask_prompt_models(role, serialize_prompt_models(session_row))
        except Exception as exc:
            prompt_models = {"items": [], "default_model": {"providerID": "", "modelID": ""}, "error": str(exc)}
        return mask_for_role(role, {
            "draft": serialize_skill_builder_payload(session_row, run_row),
            "skill": get_openflow_skill(),
            "prompt_models": prompt_models,
        })

    @router.put("/api/openflow/analysis/skill-builder")
    async def openflow_analysis_skill_builder_save(request: Request, payload: OpenFlowSkillBuilderPayload) -> dict[str, Any]:
        role = request_role(request)
        session_row = safe_session(payload.session_id)
        updated = now_ms()
        content = payload.content.strip()
        skill_model = normalize_skill_model(payload)
        if role != "admin":
            skill_model, _ = resolve_skill_model(session_row, payload, role)
        ctx.openflow_repo.update_for_session(
            int(session_row["id"]),
            generated_skill_content=content,
            **skill_model,
            skill_version_name=payload.version_name.strip(),
            skill_version_notes=payload.version_notes.strip(),
            updated_at=updated,
        )
        if content:
            ctx.skill_repo.upsert("openflow", OPENFLOW_SKILL_KIND, DEFAULT_OPENFLOW_SKILL["title"], content, updated)
        return mask_for_role(role, {
            "ok": True,
            "draft": serialize_skill_builder_payload(safe_session(int(session_row["id"])), ensure_openflow_run(int(session_row["id"]))),
            "skill": get_openflow_skill(),
        })

    @router.post("/api/openflow/analysis/generate-skill")
    async def openflow_analysis_generate_skill(request: Request, payload: OpenFlowSkillBuilderGeneratePayload) -> dict[str, Any]:
        role = request_role(request)
        session_row = safe_session(payload.session_id)
        run_row = ensure_openflow_run(int(session_row["id"]))
        skill_model, prompt_models = resolve_skill_model(session_row, payload, role)
        generated = generate_skill_with_model(session_row, run_row, skill_model)
        ctx.openflow_repo.update_for_session(
            int(session_row["id"]),
            generated_skill_content=generated,
            **skill_model,
            updated_at=now_ms(),
        )
        add_session_event(
            int(session_row["id"]),
            "openflow.analysis.skill.generated",
            {"provider": skill_model["skill_model_provider"], "model": skill_model["skill_model_id"]},
        )
        return mask_for_role(role, {
            "ok": True,
            "draft": serialize_skill_builder_payload(safe_session(int(session_row["id"])), ensure_openflow_run(int(session_row["id"]))),
            "skill": get_openflow_skill(),
            "prompt_models": prompt_models,
            "used_skill_model_provider": skill_model["skill_model_provider"],
            "used_skill_model_id": skill_model["skill_model_id"],
        })

    @router.post("/api/openflow/analysis/skill-builder/versions")
    async def openflow_analysis_skill_builder_save_version(request: Request, payload: OpenFlowSkillBuilderPayload) -> dict[str, Any]:
        role = request_role(request)
        session_row = safe_session(payload.session_id)
        run_row = ensure_openflow_run(int(session_row["id"]))
        versions = serialize_versions(str(run_row.get("skill_versions_json") or "[]"))
        version_id = f"sv{now_ms()}"
        version_name = payload.version_name.strip() or version_id
        skill_model = {
            "skill_model_provider": payload.skill_model_provider.strip(),
            "skill_model_id": payload.skill_model_id.strip(),
        }
        if role != "admin":
            skill_model, _ = resolve_skill_model(session_row, payload, role)
        versions.insert(0, {
            "id": version_id,
            "name": version_name,
            "notes": payload.version_notes.strip(),
            "content": payload.content.strip(),
            "skill_model_provider": skill_model["skill_model_provider"],
            "skill_model_id": skill_model["skill_model_id"],
            "updated_at": now_ms(),
        })
        ctx.openflow_repo.update_for_session(
            int(session_row["id"]),
            skill_versions_json=json.dumps(versions, ensure_ascii=False),
            skill_version_name=version_name,
            skill_version_notes=payload.version_notes.strip(),
            updated_at=now_ms(),
        )
        return mask_for_role(role, {"ok": True, "versions": versions})

    @router.post("/api/openflow/analysis/skill-builder/versions/load")
    async def openflow_analysis_skill_builder_load_version(request: Request, payload: OpenFlowSkillVersionLoadPayload) -> dict[str, Any]:
        role = request_role(request)
        session_row = safe_session(payload.session_id)
        run_row = ensure_openflow_run(int(session_row["id"]))
        versions = serialize_versions(str(run_row.get("skill_versions_json") or "[]"))
        version = next((item for item in versions if str(item.get("id")) == payload.version_id), None)
        if not version:
            raise HTTPException(status_code=404, detail="Skill version not found")
        ctx.openflow_repo.update_for_session(
            int(session_row["id"]),
            generated_skill_content=str(version.get("content") or ""),
            skill_model_provider=str(version.get("skill_model_provider") or ""),
            skill_model_id=str(version.get("skill_model_id") or ""),
            skill_version_name=str(version.get("name") or ""),
            skill_version_notes=str(version.get("notes") or ""),
            updated_at=now_ms(),
        )
        return mask_for_role(role, {"ok": True, "draft": serialize_skill_builder_payload(safe_session(int(session_row["id"])), ensure_openflow_run(int(session_row["id"])))})

    @router.post("/api/openflow/analysis/skill-builder/versions/delete")
    async def openflow_analysis_skill_builder_delete_version(request: Request, payload: OpenFlowSkillVersionDeletePayload) -> dict[str, Any]:
        role = request_role(request)
        session_row = safe_session(payload.session_id)
        run_row = ensure_openflow_run(int(session_row["id"]))
        versions = serialize_versions(str(run_row.get("skill_versions_json") or "[]"))
        filtered = [item for item in versions if str(item.get("id")) != payload.version_id]
        if len(filtered) == len(versions):
            raise HTTPException(status_code=404, detail="Skill version not found")
        ctx.openflow_repo.update_for_session(int(session_row["id"]), skill_versions_json=json.dumps(filtered, ensure_ascii=False), updated_at=now_ms())
        return mask_for_role(role, {"ok": True, "versions": filtered})

    @router.put("/api/openflow/analysis/skill")
    async def openflow_analysis_skill_save(payload: OpenFlowPromptSavePayload) -> dict[str, Any]:
        ctx.skill_repo.upsert("openflow", OPENFLOW_SKILL_KIND, DEFAULT_OPENFLOW_SKILL["title"], payload.content.strip(), now_ms())
        ctx.event("info", "openflow", "OpenFlow analysis skill updated", {"kind": OPENFLOW_SKILL_KIND})
        return {"ok": True}

    @router.post("/api/openflow/analysis/skill/restore-default")
    async def openflow_analysis_skill_restore() -> dict[str, Any]:
        ctx.skill_repo.upsert(
            "openflow",
            OPENFLOW_SKILL_KIND,
            DEFAULT_OPENFLOW_SKILL["title"],
            build_openflow_skill_content(),
            now_ms(),
        )
        ctx.event("info", "openflow", "OpenFlow analysis skill restored", {"kind": OPENFLOW_SKILL_KIND})
        return {"ok": True}

    return router
