from __future__ import annotations

import asyncio
import hashlib
import json
import queue
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from opcrew_backend.context import now_ms
from opcrew_backend.model_policy import (
    SURFACE_KOUBO_ASSET_AGENT_CHAT,
    SURFACE_KOUBO_COMPOSER_AGENT_CHAT,
    SURFACE_KOUBO_IMAGE_PLAN_AGENT_CHAT,
    SURFACE_KOUBO_STORYBOARD_EDIT_AGENT_CHAT,
    SURFACE_KOUBO_VIDEO_PLAN_AGENT_CHAT,
    mask_model_fields_for_role,
    mask_prompt_models_for_role,
    request_role,
)
from opcrew_backend.routes.auth import AUTH_ROLE_USER

from .agent_chat_common import (
    KOUBO_AGENT_CHAT_DISABLED_TOOLS,
    KOUBO_AGENT_CHAT_MESSAGE_LIMIT,
    opencode_event_has_tool_use,
    safe_opencode_message,
    sanitize_opencode_event,
)
from .prompt_agent_result import (
    extract_prompt_agent_results,
    prompt_agent_normalize_result,
    prompt_agent_validate_result_sources,
)


AGENT_CHAT_ROOT_REL = "SessionContext/AgentChats"
AGENT_CHAT_SCHEMA = "koubo_storyboard_agent_chat_0.1"
AGENT_CHAT_VIDEO_GENERATION_TAG = "VIDEO_GENERATION_REQUEST"
PROMPT_AGENT_ROOT_REL = "SessionContext/PromptAgent"
PROMPT_AGENT_VERSION_SCHEMA = "koubo_prompt_agent_version_0.1"
PROMPT_AGENT_CRITIQUE_SCHEMA = "koubo_prompt_agent_critique_0.1"
PROMPT_AGENT_APPLIED_SCHEMA = "koubo_prompt_agent_applied_0.1"
PROMPT_AGENT_APPLY_TARGETS = {"images", "images-agent", "videos", "videos-agent"}
PROMPT_KNOWLEDGE_ROOT = Path(__file__).resolve().parents[4] / "ToolLibrary" / "PromptKnowledge"
PROMPT_KNOWLEDGE_FTS = PROMPT_KNOWLEDGE_ROOT / "index" / "fts.sqlite"
AGENT_CHAT_MODEL_ROLE = AUTH_ROLE_USER
AGENT_CHAT_KEYS = {"storyboard_edit", "image_plan", "video_plan", "composer", "asset_audio", "asset_video", "prompt_agent"}
AGENT_CHAT_SURFACES = {
    "storyboard_edit": SURFACE_KOUBO_STORYBOARD_EDIT_AGENT_CHAT,
    "image_plan": SURFACE_KOUBO_IMAGE_PLAN_AGENT_CHAT,
    "video_plan": SURFACE_KOUBO_VIDEO_PLAN_AGENT_CHAT,
    "composer": SURFACE_KOUBO_COMPOSER_AGENT_CHAT,
    "asset_audio": SURFACE_KOUBO_ASSET_AGENT_CHAT,
    "asset_video": SURFACE_KOUBO_ASSET_AGENT_CHAT,
    "prompt_agent": SURFACE_KOUBO_ASSET_AGENT_CHAT,
}
AGENT_CHAT_TITLES = {
    "storyboard_edit": "Koubo StoryBoard Edit Agent",
    "image_plan": "Koubo ImagePlan Agent",
    "video_plan": "Koubo VideoPlan Agent",
    "composer": "Koubo Composer Diagnosis Agent",
    "asset_audio": "Koubo Asset Library Audio Agent",
    "asset_video": "Koubo Asset Library Video Agent",
    "prompt_agent": "Koubo Prompt Optimization Agent",
}


def _text(value: Any, default: str = "") -> str:
    if value is None or value == "":
        value = default
    return str(value or "").strip()


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def register_agent_chat_routes(router: APIRouter, deps: Any) -> None:
    agent_chat_generation_claim_lock = threading.Lock()
    prompt_knowledge_index_lock = threading.Lock()

    def normalize_agent_key(agent_key: str) -> str:
        key = _text(agent_key)
        if key not in AGENT_CHAT_KEYS:
            raise HTTPException(status_code=404, detail="Koubo agent not found")
        return key

    def normalize_agent_generation_key_list(values: Any) -> list[str]:
        keys: list[str] = []
        for item in values if isinstance(values, list) else []:
            key = _text(item)
            if key and key not in keys:
                keys.append(key)
        return keys[-200:]

    def normalize_agent_video_context_references(values: Any, kind: str, limit: int) -> list[dict[str, str]]:
        refs: list[dict[str, str]] = []
        seen: set[str] = set()
        for item in values if isinstance(values, list) else []:
            rel_path = _text((item.get("path") or item.get("id")) if isinstance(item, dict) else item)
            if not rel_path or rel_path in seen:
                continue
            seen.add(rel_path)
            payload = {
                "path": rel_path,
                "kind": kind,
            }
            if isinstance(item, dict):
                label = _text(item.get("label") or item.get("filename"))
                role = _text(item.get("role") or item.get("reference_role"))
                if label:
                    payload["label"] = label[:240]
                if role:
                    payload["role"] = role[:80]
                    payload["reference_role"] = role[:80]
            refs.append(payload)
            if len(refs) >= limit:
                break
        return refs

    def normalize_agent_video_request_context(values: Any) -> dict[str, Any]:
        context = values if isinstance(values, dict) else {}
        settings = context.get("settings") if isinstance(context.get("settings"), dict) else {}
        return {
            "sent_at": _int(context.get("sent_at")),
            "reference_images": normalize_agent_video_context_references(context.get("reference_images"), "image", 8),
            "reference_audios": normalize_agent_video_context_references(context.get("reference_audios"), "audio", 4),
            "reference_videos": normalize_agent_video_context_references(context.get("reference_videos"), "video", 4),
            "settings": {
                "aspect": _text(settings.get("aspect")),
                "duration": _int(settings.get("duration")),
                "reference_mode": _text(settings.get("reference_mode") or settings.get("referenceMode")),
                "provider": _text(settings.get("provider")),
                "model": _text(settings.get("model")),
                "agentVideoAlias": _text(settings.get("agentVideoAlias") or settings.get("agent_video_alias")),
                "operation": _text(settings.get("operation")),
                "video_thread_id": _text(settings.get("video_thread_id") or settings.get("thread_id")),
                "parent_turn_id": _text(settings.get("parent_turn_id") or settings.get("video_turn_id")),
                "client_action_id": _text(settings.get("client_action_id")),
                "stateful": bool(settings.get("stateful")),
            },
        }

    def normalize_agent_video_context_from_client(client_context: dict[str, Any], sent_at: int) -> dict[str, Any]:
        settings = client_context.get("video_generation_settings") if isinstance(client_context.get("video_generation_settings"), dict) else {}
        selected_assets = client_context.get("selected_reference_assets") if isinstance(client_context.get("selected_reference_assets"), list) else []
        images = client_context.get("selected_reference_images") if isinstance(client_context.get("selected_reference_images"), list) else []
        audios = client_context.get("selected_reference_audios") if isinstance(client_context.get("selected_reference_audios"), list) else []
        videos = client_context.get("selected_reference_videos") if isinstance(client_context.get("selected_reference_videos"), list) else []
        if selected_assets:
            images = [*images, *[item for item in selected_assets if isinstance(item, dict) and "image" in _text(item.get("kind") or item.get("asset_type")).lower()]]
            audios = [*audios, *[item for item in selected_assets if isinstance(item, dict) and "audio" in _text(item.get("kind") or item.get("asset_type")).lower()]]
            videos = [*videos, *[item for item in selected_assets if isinstance(item, dict) and "video" in _text(item.get("kind") or item.get("asset_type")).lower()]]
        return normalize_agent_video_request_context({
            "sent_at": sent_at,
            "reference_images": images,
            "reference_audios": audios,
            "reference_videos": videos,
            "settings": settings,
        })

    def normalize_agent_video_generation_event_list(values: Any) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for item in values if isinstance(values, list) else []:
            if not isinstance(item, dict):
                continue
            generation_id = _text(item.get("agent_generation_id") or item.get("request_id"))
            status = _text(item.get("status"))
            if not generation_id or status not in {"started", "completed", "failed"}:
                continue
            event: dict[str, Any] = {
                "status": status,
                "agent_generation_id": generation_id[:240],
                "chat_opencode_session_id": _text(item.get("chat_opencode_session_id"))[:240],
                "agent_message_id": _text(item.get("agent_message_id"))[:240],
                "title": _text(item.get("title"))[:240],
                "duration": item.get("duration"),
                "aspect": _text(item.get("aspect"))[:16],
                "reference_count": _int(item.get("reference_count")),
                "reference_image_count": _int(item.get("reference_image_count")),
                "reference_audio_count": _int(item.get("reference_audio_count")),
                "reference_video_count": _int(item.get("reference_video_count")),
                "prompt_length": _int(item.get("prompt_length")),
                "updated_at": _int(item.get("updated_at")) or now_ms(),
            }
            if _text(item.get("request_id")):
                event["request_id"] = _text(item.get("request_id"))[:240]
            if item.get("elapsed_seconds") is not None:
                event["elapsed_seconds"] = item.get("elapsed_seconds")
            if status == "failed":
                event["status_code"] = _int(item.get("status_code"))
                detail = item.get("detail")
                if isinstance(detail, dict):
                    event["detail"] = detail
                elif _text(detail):
                    event["detail"] = _text(detail)[:1200]
            if status == "completed":
                asset = item.get("asset") if isinstance(item.get("asset"), dict) else {}
                compact_asset = {
                    key: asset.get(key)
                    for key in ("id", "path", "label", "filename", "asset_type", "kind", "source", "aspect", "duration", "duration_seconds", "created_at")
                    if asset.get(key) is not None
                }
                if compact_asset:
                    event["asset"] = compact_asset
                if _text(item.get("output")):
                    event["output"] = _text(item.get("output"))
                for key in ("video_thread_id", "video_turn_id", "parent_turn_id", "client_action_id", "provider_state_status", "provider_state_expires_at"):
                    if item.get(key) is not None:
                        event[key] = item.get(key)
                if item.get("can_continue") is not None:
                    event["can_continue"] = bool(item.get("can_continue"))
            events.append(event)
        deduped: dict[str, dict[str, Any]] = {}
        for event in events:
            deduped[event["agent_generation_id"]] = event
        return list(deduped.values())[-200:]

    def prompt_agent_root(task: dict[str, Any]) -> Path:
        return deps.workspace_for(task) / PROMPT_AGENT_ROOT_REL

    def prompt_agent_rel(*parts: str) -> str:
        return "/".join([PROMPT_AGENT_ROOT_REL, *parts])

    def prompt_agent_path(task: dict[str, Any], *parts: str) -> Path:
        return prompt_agent_root(task).joinpath(*parts)

    def prompt_agent_slug(value: Any, fallback_prefix: str) -> str:
        raw = _text(value)
        if re.fullmatch(r"[A-Za-z0-9_-]{1,120}", raw):
            return raw
        digest = hashlib.sha256(f"{fallback_prefix}:{time.time_ns()}".encode("utf-8")).hexdigest()[:8]
        return f"{fallback_prefix}_{now_ms()}_{digest}"

    def prompt_agent_valid_retrieval_id(value: Any) -> str:
        retrieval_id = _text(value)
        return retrieval_id if re.fullmatch(r"retrieval_\d+_[a-z0-9]{4,}", retrieval_id) else ""

    def prompt_agent_message_id(message: dict[str, Any]) -> str:
        info = message.get("info") if isinstance(message.get("info"), dict) else {}
        raw = _text(info.get("id") or message.get("id"))
        if raw:
            return prompt_agent_slug(raw, "message")
        digest = hashlib.sha256(agent_chat_message_text(message).encode("utf-8")).hexdigest()[:12]
        return f"message_{digest}"

    def prompt_agent_message_completed_at(message: dict[str, Any]) -> int:
        info = message.get("info") if isinstance(message.get("info"), dict) else {}
        time_info = info.get("time") if isinstance(info.get("time"), dict) else {}
        for key in ("completed", "created"):
            value = _int(time_info.get(key))
            if value:
                return value * 1000 if value < 100_000_000_000 else value
        return 0

    def prompt_agent_user_text_hash(message: str) -> str:
        return hashlib.sha256(_text(message).encode("utf-8")).hexdigest()[:16]

    def prompt_agent_request_id(sent_at: int, user_text_hash: str, retrieval_id: str) -> str:
        digest = hashlib.sha256(f"{sent_at}:{user_text_hash}:{retrieval_id}".encode("utf-8")).hexdigest()[:12]
        return f"request_{sent_at}_{digest}"

    def normalize_prompt_agent_request_list(values: Any) -> list[dict[str, Any]]:
        requests: list[dict[str, Any]] = []
        for item in values if isinstance(values, list) else []:
            if not isinstance(item, dict):
                continue
            request_id = prompt_agent_slug(item.get("request_id"), "request")
            sent_at = _int(item.get("sent_at"))
            if not sent_at:
                continue
            status = _text(item.get("status"), "pending")
            if status not in {"pending", "consumed", "failed", "expired"}:
                status = "pending"
            record = {
                "request_id": request_id,
                "sent_at": sent_at,
                "user_text_hash": _text(item.get("user_text_hash"))[:64],
                "retrieval_id": prompt_agent_valid_retrieval_id(item.get("retrieval_id")),
                "status": status,
            }
            consumed_by = _text(item.get("consumed_by_message_id"))
            if consumed_by:
                record["consumed_by_message_id"] = consumed_by[:160]
            consumed_at = _int(item.get("consumed_at"))
            if consumed_at:
                record["consumed_at"] = consumed_at
            failed_at = _int(item.get("failed_at"))
            if failed_at:
                record["failed_at"] = failed_at
            expire_reason = _text(item.get("expire_reason"))
            if expire_reason:
                record["expire_reason"] = expire_reason[:160]
            requests.append(record)
        return requests[-200:]

    def prompt_agent_validate_sources(task: dict[str, Any], result: dict[str, Any], retrieval_id: str = "") -> tuple[dict[str, Any], dict[str, Any]]:
        allowed_doc_ids: set[str] = set()
        retrieval_id = prompt_agent_valid_retrieval_id(retrieval_id)
        if retrieval_id:
            retrieval = read_prompt_agent_retrieval(task, retrieval_id)
            for item in retrieval.get("items") if isinstance(retrieval.get("items"), list) else []:
                doc_id = _text(item.get("doc_id") if isinstance(item, dict) else "")
                if doc_id:
                    allowed_doc_ids.add(doc_id)
        return prompt_agent_validate_result_sources(result, allowed_doc_ids)

    def prompt_agent_retrieval_id_from_context(client_context: dict[str, Any]) -> str:
        knowledge = client_context.get("knowledge") if isinstance(client_context.get("knowledge"), dict) else {}
        return prompt_agent_valid_retrieval_id(knowledge.get("retrieval_id"))

    def assert_prompt_agent_surface_access(task: dict[str, Any], request: Request) -> None:
        # There is no separate write permission helper for this surface today; touching the
        # same model-policy path keeps these routes aligned with asset agent chat access.
        agent_chat_prompt_models(task, request_role(request), "prompt_agent")

    def prompt_knowledge_normalized_files() -> list[Path]:
        normalized_dir = PROMPT_KNOWLEDGE_ROOT / "normalized"
        if not normalized_dir.is_dir():
            return []
        # Seed first so hand-curated docs win on doc_id collision; ingested
        # sources (other *.jsonl) follow in stable name order.
        return sorted(normalized_dir.glob("*.jsonl"), key=lambda path: (path.name != "seed_rules.jsonl", path.name))

    def prompt_knowledge_source_mtime() -> int:
        mtimes = [int(path.stat().st_mtime) for path in prompt_knowledge_normalized_files() if path.exists()]
        return max(mtimes) if mtimes else 0

    def prompt_knowledge_docs() -> list[dict[str, Any]]:
        docs: list[dict[str, Any]] = []
        seen: set[str] = set()
        for path in prompt_knowledge_normalized_files():
            try:
                text = path.read_text(encoding="utf-8")
            except Exception:
                continue
            for line in text.splitlines():
                raw = line.strip()
                if not raw:
                    continue
                try:
                    payload = json.loads(raw)
                except Exception:
                    continue
                if not isinstance(payload, dict):
                    continue
                doc_id = _text(payload.get("doc_id"))
                if not doc_id or doc_id in seen:
                    continue
                seen.add(doc_id)
                docs.append(payload)
        return docs

    def prompt_knowledge_doc_text(doc: dict[str, Any]) -> str:
        pieces = [
            _text(doc.get("source_title")),
            _text(doc.get("summary")),
            _text(doc.get("content")),
            " ".join(str(item) for item in doc.get("tags") or []),
        ]
        for rule in doc.get("rules") if isinstance(doc.get("rules"), list) else []:
            if isinstance(rule, dict):
                pieces.append(_text(rule.get("text")))
        return "\n".join(piece for piece in pieces if piece)

    def ensure_prompt_knowledge_index() -> None:
        PROMPT_KNOWLEDGE_FTS.parent.mkdir(parents=True, exist_ok=True)
        source_mtime = prompt_knowledge_source_mtime()

        def needs_rebuild() -> bool:
            if not PROMPT_KNOWLEDGE_FTS.exists():
                return True
            try:
                with sqlite3.connect(PROMPT_KNOWLEDGE_FTS) as conn:
                    row = conn.execute("SELECT value FROM meta WHERE key = 'source_mtime'").fetchone()
                    return not row or int(row[0] or 0) != source_mtime
            except Exception:
                return True

        if not needs_rebuild():
            return
        with prompt_knowledge_index_lock:
            if not needs_rebuild():
                return
            docs = prompt_knowledge_docs()
            tmp_path = PROMPT_KNOWLEDGE_FTS.with_name(f".{PROMPT_KNOWLEDGE_FTS.name}.{time.time_ns()}.tmp")
            try:
                with sqlite3.connect(tmp_path) as conn:
                    conn.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                    conn.execute("""
                        CREATE TABLE prompt_docs(
                            doc_id TEXT PRIMARY KEY,
                            chunk_id TEXT,
                            title TEXT,
                            source_type TEXT,
                            trust_level TEXT,
                            model_family TEXT,
                            provider TEXT,
                            summary TEXT,
                            content TEXT,
                            rules_json TEXT,
                            tags_json TEXT
                        )
                    """)
                    conn.execute("CREATE VIRTUAL TABLE prompt_docs_fts USING fts5(doc_id UNINDEXED, search_text, tokenize='trigram')")
                    for doc in docs:
                        doc_id = _text(doc.get("doc_id"))
                        families = doc.get("model_family") if isinstance(doc.get("model_family"), list) else []
                        conn.execute(
                            "INSERT OR REPLACE INTO prompt_docs(doc_id, chunk_id, title, source_type, trust_level, model_family, provider, summary, content, rules_json, tags_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (
                                doc_id,
                                _text(doc.get("chunk_id")),
                                _text(doc.get("source_title")),
                                _text(doc.get("source_type")),
                                _text(doc.get("trust_level")),
                                json.dumps(families, ensure_ascii=False),
                                _text(doc.get("provider")),
                                _text(doc.get("summary")),
                                _text(doc.get("content")),
                                json.dumps(doc.get("rules") or [], ensure_ascii=False),
                                json.dumps(doc.get("tags") or [], ensure_ascii=False),
                            ),
                        )
                        conn.execute("INSERT INTO prompt_docs_fts(doc_id, search_text) VALUES (?, ?)", (doc_id, prompt_knowledge_doc_text(doc)))
                    conn.execute("INSERT INTO meta(key, value) VALUES ('source_mtime', ?)", (str(source_mtime),))
                    conn.commit()
                tmp_path.replace(PROMPT_KNOWLEDGE_FTS)
            finally:
                try:
                    if tmp_path.exists():
                        tmp_path.unlink()
                except Exception:
                    pass

    def prompt_knowledge_trust_rank(value: str) -> int:
        return {
            "local_experience": 520,
            "official": 500,
            "community_high_signal": 320,
            "community_article": 140,
            "experimental": 60,
        }.get(_text(value), 0)

    def prompt_knowledge_generic_penalty(trust_level: str, families: list[Any]) -> int:
        normalized = {_text(family) for family in families if _text(family)}
        if normalized and normalized <= {"general"}:
            return 220 if _text(trust_level) in {"community_article", "experimental"} else 120
        return 0

    def prompt_knowledge_score(
        trust_level: str,
        families: list[Any],
        item_provider: str,
        requested_family: str,
        requested_provider: str,
        fts_rank: float = 0.0,
    ) -> float:
        family_bonus = 0
        normalized_families = {_text(family) for family in families if _text(family)}
        if requested_family:
            if requested_family in normalized_families:
                family_bonus = 140
            elif "general" in normalized_families:
                family_bonus = 30
        provider_bonus = 80 if requested_provider and item_provider and requested_provider == item_provider else 0
        return (
            prompt_knowledge_trust_rank(trust_level)
            + family_bonus
            + provider_bonus
            - prompt_knowledge_generic_penalty(trust_level, families)
            - float(fts_rank or 0)
        )

    def prompt_knowledge_bucket(item: dict[str, Any], provider: str) -> str:
        # Multi-route diversity buckets (design doc 7.2 P1): keep results from
        # mixing official docs, provider-specific experience, local experience,
        # community signal and generic structure instead of one type dominating.
        trust = _text(item.get("trust_level"))
        item_provider = _text(item.get("provider"))
        families = item.get("model_family") if isinstance(item.get("model_family"), list) else []
        if trust == "official":
            return "official"
        if provider and item_provider and provider == item_provider:
            return "provider_experience"
        if trust == "local_experience":
            return "local_experience"
        if trust in {"community_high_signal", "community_article"}:
            return "community"
        if "general" in families:
            return "general"
        return "other"

    def prompt_knowledge_multi_route(ranked: list[dict[str, Any]], provider: str, limit: int) -> list[dict[str, Any]]:
        caps = {"official": 3, "provider_experience": 3, "local_experience": 3, "community": 2, "general": 1, "other": limit}
        counts = {key: 0 for key in caps}
        selected: list[dict[str, Any]] = []
        chosen: set[str] = set()
        # First pass: diversity-capped pick in score order.
        for item in ranked:
            if len(selected) >= limit:
                break
            bucket = prompt_knowledge_bucket(item, provider)
            if counts[bucket] >= caps[bucket]:
                continue
            counts[bucket] += 1
            chosen.add(_text(item.get("doc_id")))
            selected.append({**item, "bucket": bucket})
        # Second pass: top up by score if buckets left us short of limit.
        for item in ranked:
            if len(selected) >= limit:
                break
            doc_id = _text(item.get("doc_id"))
            if doc_id in chosen:
                continue
            chosen.add(doc_id)
            selected.append({**item, "bucket": prompt_knowledge_bucket(item, provider)})
        return selected

    def prompt_knowledge_fts_query(query: str) -> str:
        # FTS5 trigram tokenizer matches substrings, so emit ASCII words plus
        # sliding 3-char windows over CJK runs. This recovers Chinese recall the
        # default unicode61 tokenizer misses (it only splits on spaces/punct).
        # Runs shorter than 3 chars produce no trigram; the family/provider
        # fallback in prompt_knowledge_search_items covers those queries.
        text = _text(query)
        pieces: list[str] = []
        for token in re.findall(r"[A-Za-z0-9_]{2,}", text):
            if token not in pieces:
                pieces.append(token)
        for run in re.findall(r"[一-鿿]+", text):
            for index in range(len(run) - 2):
                gram = run[index:index + 3]
                if gram not in pieces:
                    pieces.append(gram)
        return " OR ".join(f'"{piece}"' for piece in pieces[:64])

    def prompt_knowledge_search_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
        query = _text(payload.get("query"))
        model_family = _text(payload.get("model_family"))
        provider = _text(payload.get("provider"))
        limit = max(1, min(_int(payload.get("limit")) or 8, 12))
        if not query:
            query = "prompt structure negative prompt reference role camera stability"
        try:
            ensure_prompt_knowledge_index()
            with sqlite3.connect(PROMPT_KNOWLEDGE_FTS) as conn:
                conn.row_factory = sqlite3.Row
                fts_query = prompt_knowledge_fts_query(query)
                rows = conn.execute(
                    """
                    SELECT d.*, bm25(prompt_docs_fts) AS rank
                    FROM prompt_docs_fts
                    JOIN prompt_docs d ON d.doc_id = prompt_docs_fts.doc_id
                    WHERE prompt_docs_fts MATCH ?
                    ORDER BY rank
                    LIMIT 40
                    """,
                    (fts_query,),
                ).fetchall() if fts_query else []
        except Exception:
            rows = []
        items: list[dict[str, Any]] = []
        if rows:
            for row in rows:
                families = json.loads(row["model_family"] or "[]")
                if model_family and model_family not in families and "general" not in families:
                    continue
                if provider and row["provider"] and provider != row["provider"]:
                    continue
                score = prompt_knowledge_score(row["trust_level"], families, row["provider"], model_family, provider, float(row["rank"] or 0))
                items.append({
                    "doc_id": row["doc_id"],
                    "chunk_id": row["chunk_id"],
                    "title": row["title"],
                    "source_type": row["source_type"],
                    "trust_level": row["trust_level"],
                    "model_family": families,
                    "provider": row["provider"],
                    "summary": row["summary"],
                    "rules": json.loads(row["rules_json"] or "[]"),
                    "score": round(score, 3),
                })
        if not items:
            for doc in prompt_knowledge_docs():
                families = doc.get("model_family") if isinstance(doc.get("model_family"), list) else []
                if model_family and model_family not in families and "general" not in families:
                    continue
                if provider and _text(doc.get("provider")) and provider != _text(doc.get("provider")):
                    continue
                items.append({
                    "doc_id": _text(doc.get("doc_id")),
                    "chunk_id": _text(doc.get("chunk_id")),
                    "title": _text(doc.get("source_title")),
                    "source_type": _text(doc.get("source_type")),
                    "trust_level": _text(doc.get("trust_level")),
                    "model_family": families,
                    "provider": _text(doc.get("provider")),
                    "summary": _text(doc.get("summary")),
                    "rules": doc.get("rules") if isinstance(doc.get("rules"), list) else [],
                    "score": round(
                        prompt_knowledge_score(
                            _text(doc.get("trust_level")),
                            families,
                            _text(doc.get("provider")),
                            model_family,
                            provider,
                        ),
                        3,
                    ),
                })
        deduped: dict[str, dict[str, Any]] = {}
        for item in items:
            doc_id = _text(item.get("doc_id"))
            if doc_id and (doc_id not in deduped or float(item.get("score") or 0) > float(deduped[doc_id].get("score") or 0)):
                deduped[doc_id] = item
        ranked = sorted(deduped.values(), key=lambda item: float(item.get("score") or 0), reverse=True)
        return prompt_knowledge_multi_route(ranked, provider, limit)

    def write_prompt_agent_retrieval(task: dict[str, Any], request_payload: dict[str, Any], items: list[dict[str, Any]]) -> dict[str, Any]:
        retrieval_id = prompt_agent_slug("", "retrieval")
        payload = {
            "schema_version": "koubo_prompt_agent_retrieval_0.1",
            "retrieval_id": retrieval_id,
            "task_id": int(task["id"]),
            "session_id": int(task["session_id"]),
            "created_at": now_ms(),
            "request": {
                "query": _text(request_payload.get("query"))[:4000],
                "mode": _text(request_payload.get("mode")),
                "model_family": _text(request_payload.get("model_family")),
                "provider": _text(request_payload.get("provider")),
                "model": _text(request_payload.get("model")),
                "limit": _int(request_payload.get("limit")) or 8,
            },
            "items": items,
        }
        rel_path = prompt_agent_rel("Retrieval", f"{retrieval_id}.json")
        deps.write_json(prompt_agent_path(task, "Retrieval", f"{retrieval_id}.json"), payload)
        return {"retrieval_id": retrieval_id, "retrieval_path": rel_path, **payload}

    def read_prompt_agent_retrieval(task: dict[str, Any], retrieval_id: str) -> dict[str, Any]:
        retrieval_id = prompt_agent_valid_retrieval_id(retrieval_id)
        if not retrieval_id:
            return {}
        root = prompt_agent_path(task, "Retrieval")
        path = root / f"{retrieval_id}.json"
        try:
            resolved_root = root.resolve()
            resolved_path = path.resolve()
            if resolved_root not in resolved_path.parents or not resolved_path.is_file():
                return {}
        except Exception:
            return {}
        payload = deps.read_json(path)
        if payload.get("schema_version") != "koubo_prompt_agent_retrieval_0.1":
            return {}
        if _int(payload.get("task_id")) != int(task["id"]) or _int(payload.get("session_id")) != int(task["session_id"]):
            return {}
        return payload

    def agent_chat_state_path(task: dict[str, Any], agent_key: str) -> tuple[str, Path]:
        rel_path = f"{AGENT_CHAT_ROOT_REL}/{agent_key}.json"
        return rel_path, deps.workspace_for(task) / rel_path

    def agent_chat_session_row(task: dict[str, Any]) -> dict[str, Any]:
        session = deps.safe_session(int(task["session_id"]))
        return {**session, "workspace_dir": str(deps.workspace_for(task))}

    def agent_chat_prompt_models(task: dict[str, Any], _role: str, agent_key: str) -> dict[str, Any]:
        surface = AGENT_CHAT_SURFACES[agent_key]
        return mask_prompt_models_for_role(deps.ctx, AGENT_CHAT_MODEL_ROLE, surface, deps.safe_prompt_models(agent_chat_session_row(task), sc=deps))

    def read_agent_chat_state(task: dict[str, Any], agent_key: str) -> dict[str, Any]:
        _rel_path, path = agent_chat_state_path(task, agent_key)
        existing = deps.read_json(path)
        if not existing:
            return {}
        if existing.get("schema_version") != AGENT_CHAT_SCHEMA:
            return {}
        if _int(existing.get("task_id")) != int(task["id"]) or _int(existing.get("session_id")) != int(task["session_id"]):
            return {}
        return existing

    def write_agent_chat_state(task: dict[str, Any], agent_key: str, state: dict[str, Any]) -> dict[str, Any]:
        rel_path, path = agent_chat_state_path(task, agent_key)
        previous = read_agent_chat_state(task, agent_key)
        timestamp = now_ms()
        if "opencode_session_id" in state or "chat_opencode_session_id" in state:
            opencode_session_id = _text(state.get("opencode_session_id") or state.get("chat_opencode_session_id"))
        else:
            opencode_session_id = _text(previous.get("opencode_session_id") or previous.get("chat_opencode_session_id"))
        payload = {
            "schema_version": AGENT_CHAT_SCHEMA,
            "agent_key": agent_key,
            "task_id": int(task["id"]),
            "session_id": int(task["session_id"]),
            "opencode_session_id": opencode_session_id,
            "chat_opencode_session_id": opencode_session_id,
            "created_at": _int(previous.get("created_at")) or timestamp,
            "updated_at": timestamp,
            "last_message_at": _int(state.get("last_message_at") if "last_message_at" in state else previous.get("last_message_at")),
        }
        if agent_key == "asset_video":
            payload["asset_video_generation_keys"] = normalize_agent_generation_key_list(state.get("asset_video_generation_keys") or previous.get("asset_video_generation_keys"))
            payload["asset_video_request_context"] = normalize_agent_video_request_context(state.get("asset_video_request_context") if "asset_video_request_context" in state else previous.get("asset_video_request_context"))
            payload["asset_video_generation_events"] = normalize_agent_video_generation_event_list(state.get("asset_video_generation_events") if "asset_video_generation_events" in state else previous.get("asset_video_generation_events"))
        if agent_key == "prompt_agent":
            payload["prompt_agent_critique_keys"] = normalize_agent_generation_key_list(state.get("prompt_agent_critique_keys") or previous.get("prompt_agent_critique_keys"))
            payload["prompt_agent_last_retrieval_id"] = _text(state.get("prompt_agent_last_retrieval_id") if "prompt_agent_last_retrieval_id" in state else previous.get("prompt_agent_last_retrieval_id"))
            payload["prompt_agent_requests"] = normalize_prompt_agent_request_list(state.get("prompt_agent_requests") if "prompt_agent_requests" in state else previous.get("prompt_agent_requests"))
        deps.write_json(path, payload)
        return {"ok": True, "path": rel_path, **payload}

    def record_agent_video_generation_event(task: dict[str, Any], status: str, properties: dict[str, Any]) -> None:
        state = read_agent_chat_state(task, "asset_video")
        events = normalize_agent_video_generation_event_list(state.get("asset_video_generation_events"))
        event = normalize_agent_video_generation_event_list([{**(properties if isinstance(properties, dict) else {}), "status": status, "updated_at": now_ms()}])
        if not event:
            return
        next_by_id = {item["agent_generation_id"]: item for item in events}
        next_by_id[event[0]["agent_generation_id"]] = event[0]
        write_agent_chat_state(task, "asset_video", {"asset_video_generation_events": list(next_by_id.values())})

    def opencode_session_not_found(exc: Exception) -> bool:
        return isinstance(exc, HTTPError) and exc.code == 404

    def clear_stale_agent_chat_session(task: dict[str, Any], agent_key: str, chat_session_id: str, source: str) -> dict[str, Any]:
        saved = write_agent_chat_state(task, agent_key, {
            "opencode_session_id": "",
            "chat_opencode_session_id": "",
            "last_message_at": 0,
        })
        deps.add_event(int(task["session_id"]), "koubo_storyboard.agent_chat.session.stale_cleared", {
            "task_id": int(task["id"]),
            "session_id": int(task["session_id"]),
            "agent_key": agent_key,
            "opencode_session_id": chat_session_id,
            "source": source,
            "path": saved.get("path"),
        })
        return saved

    def append_prompt_agent_request(task: dict[str, Any], message: str, retrieval_id: str, sent_at: int) -> dict[str, Any]:
        retrieval_id = prompt_agent_valid_retrieval_id(retrieval_id)
        user_hash = prompt_agent_user_text_hash(message)
        request_id = prompt_agent_request_id(sent_at, user_hash, retrieval_id)
        record = {
            "request_id": request_id,
            "sent_at": sent_at,
            "user_text_hash": user_hash,
            "retrieval_id": retrieval_id,
            "status": "pending",
        }
        with agent_chat_generation_claim_lock:
            state = read_agent_chat_state(task, "prompt_agent")
            requests = normalize_prompt_agent_request_list(state.get("prompt_agent_requests"))
            requests.append(record)
            saved = write_agent_chat_state(task, "prompt_agent", {
                "prompt_agent_requests": requests,
                "prompt_agent_last_retrieval_id": retrieval_id,
                "last_message_at": sent_at,
            })
        return {**record, "state": saved}

    def mark_prompt_agent_request_failed(task: dict[str, Any], request_id: str) -> None:
        request_id = _text(request_id)
        if not request_id:
            return
        with agent_chat_generation_claim_lock:
            state = read_agent_chat_state(task, "prompt_agent")
            requests = normalize_prompt_agent_request_list(state.get("prompt_agent_requests"))
            for record in requests:
                if record.get("request_id") == request_id and record.get("status") == "pending":
                    record["status"] = "failed"
                    record["failed_at"] = now_ms()
                    break
            write_agent_chat_state(task, "prompt_agent", {"prompt_agent_requests": requests})

    def expire_oldest_prompt_agent_pending_request(task: dict[str, Any], reason: str, before_ms: int = 0) -> None:
        with agent_chat_generation_claim_lock:
            state = read_agent_chat_state(task, "prompt_agent")
            requests = normalize_prompt_agent_request_list(state.get("prompt_agent_requests"))
            candidate_index = -1
            for index, record in enumerate(requests):
                if record.get("status") != "pending" or _text(record.get("consumed_by_message_id")):
                    continue
                if before_ms and _int(record.get("sent_at")) > before_ms + 10_000:
                    continue
                candidate_index = index
                break
            if candidate_index < 0:
                return
            requests[candidate_index]["status"] = "expired"
            requests[candidate_index]["failed_at"] = now_ms()
            requests[candidate_index]["expire_reason"] = _text(reason)[:160]
            write_agent_chat_state(task, "prompt_agent", {"prompt_agent_requests": requests})

    def claim_prompt_agent_request_for_message(task: dict[str, Any], message: dict[str, Any]) -> dict[str, Any]:
        message_id = prompt_agent_message_id(message)
        completed_at = prompt_agent_message_completed_at(message)
        with agent_chat_generation_claim_lock:
            state = read_agent_chat_state(task, "prompt_agent")
            requests = normalize_prompt_agent_request_list(state.get("prompt_agent_requests"))
            for record in requests:
                if _text(record.get("consumed_by_message_id")) == message_id:
                    return {**record, "match": "already_consumed"}
            candidate_index = -1
            for index, record in enumerate(requests):
                if record.get("status") != "pending" or _text(record.get("consumed_by_message_id")):
                    continue
                if completed_at and _int(record.get("sent_at")) > completed_at + 10_000:
                    continue
                candidate_index = index
                break
            if candidate_index < 0:
                return {"match": "no_pending_request", "retrieval_id": "", "message_id": message_id}
            record = requests[candidate_index]
            record["status"] = "consumed"
            record["consumed_by_message_id"] = message_id
            record["consumed_at"] = now_ms()
            write_agent_chat_state(task, "prompt_agent", {"prompt_agent_requests": requests})
            return {**record, "match": "live_fifo", "message_id": message_id}

    def claim_agent_video_generation_key(task: dict[str, Any], generation_key: str) -> bool:
        with agent_chat_generation_claim_lock:
            state = read_agent_chat_state(task, "asset_video")
            keys = normalize_agent_generation_key_list(state.get("asset_video_generation_keys"))
            if generation_key in keys:
                return False
            keys.append(generation_key)
            write_agent_chat_state(task, "asset_video", {"asset_video_generation_keys": keys})
            return True

    def claim_prompt_agent_critique_key(task: dict[str, Any], critique_key: str) -> bool:
        with agent_chat_generation_claim_lock:
            state = read_agent_chat_state(task, "prompt_agent")
            keys = normalize_agent_generation_key_list(state.get("prompt_agent_critique_keys"))
            if critique_key in keys:
                return False
            keys.append(critique_key)
            write_agent_chat_state(task, "prompt_agent", {"prompt_agent_critique_keys": keys})
            return True

    def normalize_reference_path_list(values: Any, limit: int = 4) -> list[str]:
        refs: list[str] = []
        for item in values if isinstance(values, list) else []:
            rel_path = _text(item.get("path") if isinstance(item, dict) else item)
            if rel_path and rel_path not in refs:
                refs.append(rel_path)
        return refs[:limit]

    def normalize_video_reference_mode(value: Any) -> str:
        mode = _text(value).lower()
        return mode if mode in {"input_references", "reference_to_video", "sr2"} else ""

    def agent_chat_message_text(message: dict[str, Any]) -> str:
        if _text(message.get("text")):
            return _text(message.get("text"))
        parts = message.get("parts") if isinstance(message.get("parts"), list) else []
        return "\n".join(str(part.get("text") or "") for part in parts if isinstance(part, dict) and _text(part.get("type"), "text") == "text").strip()

    def assistant_message_from_event(event: dict[str, Any], require_completed: bool = True) -> dict[str, Any] | None:
        if _text(event.get("type")) != "message.updated":
            return None
        properties = event.get("properties") if isinstance(event.get("properties"), dict) else {}
        if isinstance(properties.get("message"), dict):
            message = properties["message"]
        else:
            message = {
                "info": properties.get("info") if isinstance(properties.get("info"), dict) else {},
                "parts": properties.get("parts") if isinstance(properties.get("parts"), list) else [],
            }
        info = message.get("info") if isinstance(message.get("info"), dict) else {}
        if _text(info.get("role") or message.get("role")) != "assistant":
            return None
        time_info = info.get("time") if isinstance(info.get("time"), dict) else {}
        if require_completed and not time_info.get("completed"):
            return None
        if not agent_chat_message_text(message):
            return None
        return message

    def completed_assistant_message_from_event(event: dict[str, Any]) -> dict[str, Any] | None:
        return assistant_message_from_event(event, require_completed=True)

    def video_generation_message_from_event(event: dict[str, Any]) -> dict[str, Any] | None:
        return assistant_message_from_event(event, require_completed=False)

    def agent_video_message_time(message: dict[str, Any]) -> int:
        info = message.get("info") if isinstance(message.get("info"), dict) else {}
        time_info = info.get("time") if isinstance(info.get("time"), dict) else {}
        for key in ("completed", "created"):
            value = _int(time_info.get(key) or message.get(f"{key}_at"))
            if value:
                return value * 1000 if value < 100_000_000_000 else value
        return 0

    def agent_video_context_for_generation(task: dict[str, Any], message: dict[str, Any]) -> dict[str, Any]:
        state = read_agent_chat_state(task, "asset_video")
        context = normalize_agent_video_request_context(state.get("asset_video_request_context"))
        sent_at = _int(context.get("sent_at"))
        message_at = agent_video_message_time(message)
        if sent_at and message_at and message_at + 10_000 < sent_at:
            return {}
        return context

    def should_process_agent_video_message(task: dict[str, Any], message: dict[str, Any]) -> bool:
        state = read_agent_chat_state(task, "asset_video")
        last_message_at = _int(state.get("last_message_at"))
        message_at = agent_video_message_time(message)
        return not (last_message_at and message_at and message_at + 10_000 < last_message_at)

    def apply_agent_video_request_context(request_payload: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        settings = context.get("settings") if isinstance(context.get("settings"), dict) else {}
        patched = dict(request_payload)
        if not patched.get("reference_images"):
            patched["reference_images"] = context.get("reference_images") or []
        if not patched.get("reference_audios"):
            patched["reference_audios"] = context.get("reference_audios") or []
        if not patched.get("reference_videos"):
            patched["reference_videos"] = context.get("reference_videos") or []
        if not _text(patched.get("reference_mode")):
            patched["reference_mode"] = _text(settings.get("reference_mode"))
        if not _text(patched.get("provider")):
            patched["provider"] = _text(settings.get("provider"))
        if not _text(patched.get("model")):
            patched["model"] = _text(settings.get("model"))
        if not _text(patched.get("agentVideoAlias") or patched.get("agent_video_alias")):
            patched["agentVideoAlias"] = _text(settings.get("agentVideoAlias"))
        return patched

    def extract_agent_video_generation_requests(message_text: str, context: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        pattern = re.compile(rf"<{AGENT_CHAT_VIDEO_GENERATION_TAG}>([\s\S]*?)</{AGENT_CHAT_VIDEO_GENERATION_TAG}>", re.IGNORECASE)
        context_settings = (context or {}).get("settings") if isinstance((context or {}).get("settings"), dict) else {}
        requests: list[dict[str, Any]] = []
        for match in pattern.finditer(message_text or ""):
            try:
                payload = json.loads(match.group(1).strip())
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            prompt = _text(payload.get("prompt") or payload.get("positive_prompt"))
            if not prompt:
                continue
            aspect = _text(payload.get("aspect") or context_settings.get("aspect"), "9:16")
            if aspect not in {"9:16", "16:9"}:
                aspect = "9:16"
            duration_value = payload.get("duration") if payload.get("duration") is not None else payload.get("duration_seconds")
            try:
                duration = max(1, min(int(round(float(duration_value if duration_value is not None else context_settings.get("duration") or 4))), 30))
            except Exception:
                duration = 4
            request_payload = {
                "title": _text(payload.get("title"), "Agent generated video")[:200],
                "prompt": prompt,
                "duration": duration,
                "aspect": aspect,
                "reference_images": normalize_reference_path_list(payload.get("reference_images"), 8),
                "reference_audios": normalize_reference_path_list(payload.get("reference_audios")),
                "reference_videos": normalize_reference_path_list(payload.get("reference_videos")),
                "reference_mode": normalize_video_reference_mode(payload.get("reference_mode") or payload.get("referenceMode")),
                "provider": _text(payload.get("provider")),
                "model": _text(payload.get("model")),
                "agentVideoAlias": _text(payload.get("agentVideoAlias") or payload.get("agent_video_alias")),
                "notes": _text(payload.get("notes"))[:500],
                "operation": _text(payload.get("operation")),
                "video_thread_id": _text(payload.get("video_thread_id") or payload.get("thread_id")),
                "parent_turn_id": _text(payload.get("parent_turn_id") or payload.get("video_turn_id")),
            }
            requests.append(apply_agent_video_request_context(request_payload, context or {}))
        return requests[:1]

    def write_prompt_agent_critique(task: dict[str, Any], message: dict[str, Any], retrieval_id: str | None = None, retrieval_match: dict[str, Any] | None = None) -> dict[str, Any] | None:
        message_text = agent_chat_message_text(message)
        results = extract_prompt_agent_results(message_text)
        if not results:
            return None
        message_id = prompt_agent_message_id(message)
        critique_key = f"critique_{message_id}"
        if not claim_prompt_agent_critique_key(task, critique_key):
            return None
        result = results[-1]
        if retrieval_id is None:
            retrieval_match = claim_prompt_agent_request_for_message(task, message)
            retrieval_id = prompt_agent_valid_retrieval_id(retrieval_match.get("retrieval_id") if isinstance(retrieval_match, dict) else "")
        else:
            retrieval_id = prompt_agent_valid_retrieval_id(retrieval_id)
            retrieval_match = retrieval_match if isinstance(retrieval_match, dict) else {"match": "explicit" if retrieval_id else "empty"}
        info = message.get("info") if isinstance(message.get("info"), dict) else {}
        validation_result, source_validation = prompt_agent_validate_sources(task, result, retrieval_id)
        timestamp = now_ms()
        rel_path = prompt_agent_rel("Critiques", f"{critique_key}.json")
        payload = {
            "schema_version": PROMPT_AGENT_CRITIQUE_SCHEMA,
            "task_id": int(task["id"]),
            "session_id": int(task["session_id"]),
            "message_id": message_id,
            "agent_key": "prompt_agent",
            "mode": validation_result.get("mode"),
            "summary": validation_result.get("summary"),
            "issues": validation_result.get("issues") or [],
            "revised_prompt": validation_result.get("revised_prompt") or "",
            "negative_prompt": validation_result.get("negative_prompt") or "",
            "changes": validation_result.get("changes") or [],
            "model_notes": validation_result.get("model_notes") or [],
            "used_sources": validation_result.get("used_sources") or [],
            "source_validation": source_validation,
            "retrieval_id": retrieval_id,
            "retrieval_request_id": _text(retrieval_match.get("request_id") if isinstance(retrieval_match, dict) else ""),
            "retrieval_match": retrieval_match or {},
            "created_at": timestamp,
            "assistant_completed_at": (info.get("time") or {}).get("completed") if isinstance(info.get("time"), dict) else None,
        }
        deps.write_json(prompt_agent_path(task, "Critiques", f"{critique_key}.json"), payload)
        deps.add_event(int(task["session_id"]), "koubo_storyboard.prompt_agent.critique.saved", {
            "task_id": int(task["id"]),
            "session_id": int(task["session_id"]),
            "message_id": message_id,
            "path": rel_path,
        })
        return {"ok": True, "path": rel_path, **payload}

    def save_prompt_agent_version(task: dict[str, Any], payload: dict[str, Any], version_id: str = "", require_existing: bool = False) -> dict[str, Any]:
        timestamp = now_ms()
        version = prompt_agent_slug(version_id, "prompt_agent")
        if not version.startswith("prompt_agent_"):
            version = f"prompt_agent_{version}"
        result = prompt_agent_normalize_result({
            "mode": payload.get("mode"),
            "summary": payload.get("summary"),
            "issues": payload.get("issues") if isinstance(payload.get("issues"), list) else [],
            "revised_prompt": payload.get("revised_prompt"),
            "negative_prompt": payload.get("negative_prompt"),
            "changes": payload.get("changes") if isinstance(payload.get("changes"), list) else [],
            "model_notes": payload.get("model_notes") if isinstance(payload.get("model_notes"), list) else [],
            "used_sources": payload.get("used_sources") if isinstance(payload.get("used_sources"), list) else [],
        }) or {
            "mode": _text(payload.get("mode"), "optimize"),
            "summary": "",
            "issues": [],
            "revised_prompt": _text(payload.get("revised_prompt")),
            "negative_prompt": _text(payload.get("negative_prompt")),
            "changes": [],
            "model_notes": [],
            "used_sources": [],
        }
        retrieval_id = _text(payload.get("retrieval_id"))
        result, source_validation = prompt_agent_validate_sources(task, result, retrieval_id)
        rel_path = prompt_agent_rel("Versions", f"{version}.json")
        version_path = prompt_agent_path(task, "Versions", f"{version}.json")
        if require_existing and not version_path.is_file():
            raise HTTPException(status_code=404, detail="Prompt Agent version not found")
        previous = deps.read_json(version_path) or {}
        record = {
            "schema_version": PROMPT_AGENT_VERSION_SCHEMA,
            "version_id": version,
            "task_id": int(task["id"]),
            "session_id": int(task["session_id"]),
            "mode": result.get("mode"),
            "model_family": _text(payload.get("model_family")),
            "provider": _text(payload.get("provider")),
            "model": _text(payload.get("model")),
            "original_prompt": _text(payload.get("original_prompt"))[:20000],
            "summary": result.get("summary") or "",
            "issues": result.get("issues") or [],
            "revised_prompt": result.get("revised_prompt") or "",
            "negative_prompt": result.get("negative_prompt") or "",
            "changes": result.get("changes") or [],
            "model_notes": result.get("model_notes") or [],
            "used_sources": result.get("used_sources") or [],
            "source_validation": source_validation,
            "retrieval_id": retrieval_id,
            "created_at": _int(previous.get("created_at")) or timestamp,
            "updated_at": timestamp,
        }
        deps.write_json(version_path, record)
        deps.add_event(int(task["session_id"]), "koubo_storyboard.prompt_agent.version.saved", {
            "task_id": int(task["id"]),
            "session_id": int(task["session_id"]),
            "version_id": version,
            "path": rel_path,
        })
        return {"ok": True, "path": rel_path, **record}

    def save_prompt_agent_applied(task: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        target = _text(payload.get("target")).lower()
        if target not in PROMPT_AGENT_APPLY_TARGETS:
            raise HTTPException(status_code=400, detail="unsupported apply target")
        timestamp = now_ms()
        applied_id = prompt_agent_slug("", "applied")
        record = {
            "schema_version": PROMPT_AGENT_APPLIED_SCHEMA,
            "applied_id": applied_id,
            "task_id": int(task["id"]),
            "session_id": int(task["session_id"]),
            "target": target,
            "mode": _text(payload.get("mode"), "optimize"),
            "model_family": _text(payload.get("model_family")),
            "provider": _text(payload.get("provider")),
            "model": _text(payload.get("model")),
            "prompt": _text(payload.get("prompt") or payload.get("revised_prompt"))[:20000],
            "negative_prompt": _text(payload.get("negative_prompt"))[:8000],
            "original_prompt": _text(payload.get("original_prompt"))[:20000],
            "version_id": _text(payload.get("version_id"))[:120],
            "retrieval_id": prompt_agent_valid_retrieval_id(payload.get("retrieval_id")),
            "created_at": timestamp,
        }
        rel_path = prompt_agent_rel("Applied", f"{applied_id}.json")
        deps.write_json(prompt_agent_path(task, "Applied", f"{applied_id}.json"), record)
        deps.add_event(int(task["session_id"]), "koubo_storyboard.prompt_agent.applied.saved", {
            "task_id": int(task["id"]),
            "session_id": int(task["session_id"]),
            "applied_id": applied_id,
            "target": target,
            "path": rel_path,
        })
        return {"ok": True, "path": rel_path, **record}

    def list_prompt_agent_versions(task: dict[str, Any]) -> list[dict[str, Any]]:
        versions_dir = prompt_agent_path(task, "Versions")
        items: list[dict[str, Any]] = []
        for path in sorted(versions_dir.glob("*.json"), key=lambda item: item.stat().st_mtime if item.exists() else 0, reverse=True) if versions_dir.exists() else []:
            payload = deps.read_json(path)
            if not isinstance(payload, dict):
                continue
            if payload.get("schema_version") != PROMPT_AGENT_VERSION_SCHEMA:
                continue
            if _int(payload.get("task_id")) != int(task["id"]) or _int(payload.get("session_id")) != int(task["session_id"]):
                continue
            items.append(payload)
        return items[:200]

    def delete_prompt_agent_version(task: dict[str, Any], version_id: str) -> dict[str, Any]:
        version = prompt_agent_slug(version_id, "prompt_agent")
        if not version.startswith("prompt_agent_"):
            version = f"prompt_agent_{version}"
        version_path = prompt_agent_path(task, "Versions", f"{version}.json")
        if not version_path.is_file():
            raise HTTPException(status_code=404, detail="Prompt Agent version not found")
        payload = deps.read_json(version_path)
        if not isinstance(payload, dict):
            raise HTTPException(status_code=404, detail="Prompt Agent version not found")
        if payload.get("schema_version") != PROMPT_AGENT_VERSION_SCHEMA:
            raise HTTPException(status_code=404, detail="Prompt Agent version not found")
        if _int(payload.get("task_id")) != int(task["id"]) or _int(payload.get("session_id")) != int(task["session_id"]):
            raise HTTPException(status_code=404, detail="Prompt Agent version not found")
        version_path.unlink()
        deps.add_event(int(task["session_id"]), "koubo_storyboard.prompt_agent.version.deleted", {
            "task_id": int(task["id"]),
            "session_id": int(task["session_id"]),
            "version_id": version,
            "path": prompt_agent_rel("Versions", f"{version}.json"),
        })
        return {"ok": True, "version_id": version}

    def ensure_agent_chat_session(task: dict[str, Any], role: str, agent_key: str) -> dict[str, Any]:
        state = read_agent_chat_state(task, agent_key)
        chat_session_id = _text(state.get("opencode_session_id") or state.get("chat_opencode_session_id"))
        if chat_session_id:
            try:
                deps.opencode_client_for(agent_chat_session_row(task), sc=deps).messages(chat_session_id, limit=1, timeout=3)
            except Exception as exc:
                if opencode_session_not_found(exc):
                    clear_stale_agent_chat_session(task, agent_key, chat_session_id, "ensure_session")
                else:
                    deps.add_event(int(task["session_id"]), "koubo_storyboard.agent_chat.session_probe_skipped", {
                        "task_id": int(task["id"]),
                        "session_id": int(task["session_id"]),
                        "agent_key": agent_key,
                        "opencode_session_id": chat_session_id,
                        "detail": str(exc)[:1000],
                    })
                    return {
                        "ok": True,
                        **state,
                        "chat_opencode_session_id": chat_session_id,
                        "prompt_models": agent_chat_prompt_models(task, role, agent_key),
                    }
            else:
                return {
                    "ok": True,
                    **state,
                    "chat_opencode_session_id": chat_session_id,
                    "prompt_models": agent_chat_prompt_models(task, role, agent_key),
                }
            state = read_agent_chat_state(task, agent_key)
            chat_session_id = _text(state.get("opencode_session_id") or state.get("chat_opencode_session_id"))
        if chat_session_id:
            return {
                "ok": True,
                **state,
                "chat_opencode_session_id": chat_session_id,
                "prompt_models": agent_chat_prompt_models(task, role, agent_key),
            }
        session_row = agent_chat_session_row(task)
        title = f"{AGENT_CHAT_TITLES[agent_key]} - Task {int(task['id'])}"
        created = deps.opencode_client_for(session_row, sc=deps).create_session(title)
        chat_session_id = _text(created.get("id"))
        if not chat_session_id:
            raise HTTPException(status_code=500, detail="OpenCode did not return a chat session id")
        saved = write_agent_chat_state(task, agent_key, {
            "opencode_session_id": chat_session_id,
            "last_message_at": 0,
        })
        deps.add_event(int(task["session_id"]), "koubo_storyboard.agent_chat.session.created", {
            "task_id": int(task["id"]),
            "session_id": int(task["session_id"]),
            "agent_key": agent_key,
            "opencode_session_id": chat_session_id,
            "path": saved.get("path"),
        })
        return {**saved, "prompt_models": agent_chat_prompt_models(task, role, agent_key)}

    @router.post("/api/koubo-storyboard/tasks/{task_id}/agents/{agent_key}/chat/ensure-session")
    async def ensure_koubo_agent_chat_session(task_id: int, agent_key: str, request: Request) -> dict[str, Any]:
        key = normalize_agent_key(agent_key)
        task = deps.task_or_404(task_id)
        return ensure_agent_chat_session(task, request_role(request), key)

    @router.get("/api/koubo-storyboard/tasks/{task_id}/agents/{agent_key}/chat/messages")
    async def get_koubo_agent_chat_messages(task_id: int, agent_key: str, request: Request) -> dict[str, Any]:
        key = normalize_agent_key(agent_key)
        task = deps.task_or_404(task_id)
        role = request_role(request)
        state = read_agent_chat_state(task, key)
        chat_session_id = _text(state.get("opencode_session_id") or state.get("chat_opencode_session_id"))
        if not chat_session_id:
            return {
                "ok": True,
                "agent_key": key,
                "chat_opencode_session_id": "",
                "items": [],
                "prompt_models": agent_chat_prompt_models(task, role, key),
                **({"asset_video_generation_events": []} if key == "asset_video" else {}),
            }
        state_events = normalize_agent_video_generation_event_list(state.get("asset_video_generation_events")) if key == "asset_video" else []
        try:
            messages = deps.opencode_client_for(agent_chat_session_row(task), sc=deps).messages(chat_session_id, limit=KOUBO_AGENT_CHAT_MESSAGE_LIMIT, timeout=12)
        except Exception as exc:
            if not opencode_session_not_found(exc):
                deps.add_event(int(task["session_id"]), "koubo_storyboard.agent_chat.messages_unavailable", {
                    "task_id": int(task["id"]),
                    "session_id": int(task["session_id"]),
                    "agent_key": key,
                    "opencode_session_id": chat_session_id,
                    "detail": str(exc)[:1000],
                })
                return {
                    "ok": True,
                    "agent_key": key,
                    "chat_opencode_session_id": chat_session_id,
                    "items": [],
                    "history_unavailable": True,
                    "warning": "Agent message history is still loading. Retry shortly.",
                    **({"asset_video_generation_events": state_events} if key == "asset_video" else {}),
                }
            saved = clear_stale_agent_chat_session(task, key, chat_session_id, "messages")
            return {
                "ok": True,
                "agent_key": key,
                "chat_opencode_session_id": "",
                "items": [],
                "path": saved.get("path"),
                "recovered": True,
                **({"asset_video_generation_events": []} if key == "asset_video" else {}),
            }
        return {
            "ok": True,
            "agent_key": key,
            "chat_opencode_session_id": chat_session_id,
            "items": [safe_opencode_message(message) for message in messages],
            **({"asset_video_generation_events": state_events} if key == "asset_video" else {}),
        }

    @router.get("/api/koubo-storyboard/tasks/{task_id}/prompt-agent/versions")
    async def get_koubo_prompt_agent_versions(task_id: int, request: Request) -> dict[str, Any]:
        task = deps.task_or_404(task_id)
        assert_prompt_agent_surface_access(task, request)
        return {"ok": True, "items": list_prompt_agent_versions(task)}

    @router.post("/api/koubo-storyboard/tasks/{task_id}/prompt-agent/knowledge/search")
    async def search_koubo_prompt_agent_knowledge(task_id: int, request: Request, payload: dict[str, Any]) -> dict[str, Any]:
        task = deps.task_or_404(task_id)
        assert_prompt_agent_surface_access(task, request)
        request_payload = payload if isinstance(payload, dict) else {}
        items = prompt_knowledge_search_items(request_payload)
        retrieval = write_prompt_agent_retrieval(task, request_payload, items)
        return {
            "ok": True,
            "retrieval_id": retrieval["retrieval_id"],
            "items": items,
        }

    @router.post("/api/koubo-storyboard/tasks/{task_id}/prompt-agent/versions")
    async def create_koubo_prompt_agent_version(task_id: int, request: Request, payload: dict[str, Any]) -> dict[str, Any]:
        task = deps.task_or_404(task_id)
        assert_prompt_agent_surface_access(task, request)
        return save_prompt_agent_version(task, payload if isinstance(payload, dict) else {})

    @router.put("/api/koubo-storyboard/tasks/{task_id}/prompt-agent/versions/{version_id}")
    async def update_koubo_prompt_agent_version(task_id: int, version_id: str, request: Request, payload: dict[str, Any]) -> dict[str, Any]:
        task = deps.task_or_404(task_id)
        assert_prompt_agent_surface_access(task, request)
        return save_prompt_agent_version(task, payload if isinstance(payload, dict) else {}, version_id, require_existing=True)

    @router.delete("/api/koubo-storyboard/tasks/{task_id}/prompt-agent/versions/{version_id}")
    async def delete_koubo_prompt_agent_version(task_id: int, version_id: str, request: Request) -> dict[str, Any]:
        task = deps.task_or_404(task_id)
        assert_prompt_agent_surface_access(task, request)
        return delete_prompt_agent_version(task, version_id)

    @router.post("/api/koubo-storyboard/tasks/{task_id}/prompt-agent/applied")
    async def create_koubo_prompt_agent_applied(task_id: int, request: Request, payload: dict[str, Any]) -> dict[str, Any]:
        task = deps.task_or_404(task_id)
        assert_prompt_agent_surface_access(task, request)
        return save_prompt_agent_applied(task, payload if isinstance(payload, dict) else {})

    @router.post("/api/koubo-storyboard/tasks/{task_id}/agents/{agent_key}/chat/message")
    async def send_koubo_agent_chat_message(task_id: int, agent_key: str, request: Request, payload: dict[str, Any]) -> dict[str, Any]:
        key = normalize_agent_key(agent_key)
        task = deps.task_or_404(task_id)
        role = request_role(request)
        message = _text(payload.get("message") or payload.get("text"))
        if not message:
            raise HTTPException(status_code=400, detail="message is required")
        if len(message) > 12000:
            raise HTTPException(status_code=400, detail="message is too long")
        client_context = payload.get("client_context") if isinstance(payload.get("client_context"), dict) else {}
        prompt_agent_retrieval_id = prompt_agent_retrieval_id_from_context(client_context) if key == "prompt_agent" else ""
        if key == "prompt_agent":
            retrieval = read_prompt_agent_retrieval(task, prompt_agent_retrieval_id)
            knowledge = client_context.get("knowledge") if isinstance(client_context.get("knowledge"), dict) else {}
            client_context = {
                **client_context,
                "knowledge": {
                    **knowledge,
                    "retrieval_id": _text(retrieval.get("retrieval_id")),
                    "items": retrieval.get("items") if isinstance(retrieval.get("items"), list) else [],
                },
            }
            prompt_agent_retrieval_id = _text(retrieval.get("retrieval_id"))
        state = ensure_agent_chat_session(task, role, key)
        session_row = agent_chat_session_row(task)
        surface = AGENT_CHAT_SURFACES[key]
        provider = _text(payload.get("provider") or payload.get("providerID"))
        model_id = _text(payload.get("model") or payload.get("modelID"))
        model, prompt_models = deps.resolve_model(session_row, provider, model_id, AGENT_CHAT_MODEL_ROLE, surface, sc=deps)
        chat_session_id = _text(state.get("opencode_session_id") or state.get("chat_opencode_session_id"))
        sent_at = now_ms()
        prompt_agent_request = append_prompt_agent_request(task, message, prompt_agent_retrieval_id, sent_at) if key == "prompt_agent" else {}
        client = deps.opencode_client_for(session_row, sc=deps)

        def send_to_opencode(target_chat_session_id: str) -> None:
            client.prompt_async(
                target_chat_session_id,
                message,
                model=model,
                system=deps.agent_chat_system_prompt(key, task, client_context, sc=deps),
                tools=KOUBO_AGENT_CHAT_DISABLED_TOOLS,
            )

        try:
            send_to_opencode(chat_session_id)
        except Exception as exc:
            if opencode_session_not_found(exc):
                clear_stale_agent_chat_session(task, key, chat_session_id, "message")
                state = ensure_agent_chat_session(task, role, key)
                chat_session_id = _text(state.get("opencode_session_id") or state.get("chat_opencode_session_id"))
                try:
                    send_to_opencode(chat_session_id)
                except Exception:
                    if key == "prompt_agent":
                        mark_prompt_agent_request_failed(task, _text(prompt_agent_request.get("request_id")))
                    raise
            else:
                if key == "prompt_agent":
                    mark_prompt_agent_request_failed(task, _text(prompt_agent_request.get("request_id")))
                raise
        state_update = {
            "opencode_session_id": chat_session_id,
            "last_message_at": sent_at,
        }
        if key == "asset_video":
            state_update["asset_video_request_context"] = normalize_agent_video_context_from_client(client_context, sent_at)
        saved = write_agent_chat_state(task, key, state_update)
        deps.add_event(int(task["session_id"]), "koubo_storyboard.agent_chat.message.sent", {
            "task_id": int(task["id"]),
            "session_id": int(task["session_id"]),
            "agent_key": key,
            "opencode_session_id": chat_session_id,
            "message_length": len(message),
        })
        return {
            "ok": True,
            "agent_key": key,
            "path": saved.get("path"),
            "chat_opencode_session_id": chat_session_id,
            "model": mask_model_fields_for_role(deps.ctx, AGENT_CHAT_MODEL_ROLE, surface, model),
            "prompt_models": prompt_models,
        }

    @router.get("/api/koubo-storyboard/tasks/{task_id}/agents/{agent_key}/chat/events")
    async def stream_koubo_agent_chat_events(task_id: int, agent_key: str, request: Request) -> StreamingResponse:
        key = normalize_agent_key(agent_key)
        task = deps.task_or_404(task_id)
        role = request_role(request)
        state = ensure_agent_chat_session(task, role, key)
        chat_session_id = _text(state.get("opencode_session_id") or state.get("chat_opencode_session_id"))
        session_row = agent_chat_session_row(task)
        client = deps.opencode_client_for(session_row, sc=deps)
        stop_event = threading.Event()
        events: queue.Queue[dict[str, Any]] = queue.Queue()
        triggered_generation_keys: set[str] = set()

        def enqueue_agent_video_generation(message: dict[str, Any]) -> None:
            if key != "asset_video":
                return
            info = message.get("info") if isinstance(message.get("info"), dict) else {}
            message_id = _text(info.get("id")) or f"assistant_{now_ms()}"
            if not should_process_agent_video_message(task, message):
                return
            message_text = agent_chat_message_text(message)
            request_context = agent_video_context_for_generation(task, message)
            for request_payload in extract_agent_video_generation_requests(message_text, request_context):
                request_hash = hashlib.sha256(json.dumps(request_payload, sort_keys=True, ensure_ascii=True).encode("utf-8")).hexdigest()[:12]
                generation_key = f"{message_id}:{request_hash}"
                if generation_key in triggered_generation_keys:
                    continue
                triggered_generation_keys.add(generation_key)
                if not claim_agent_video_generation_key(task, generation_key):
                    continue
                generation_event_base = {
                    "agent_generation_id": generation_key,
                    "chat_opencode_session_id": chat_session_id,
                    "agent_message_id": message_id,
                    "title": request_payload["title"],
                    "duration": request_payload["duration"],
                    "aspect": request_payload["aspect"],
                    "reference_count": len(request_payload["reference_images"]) + len(request_payload["reference_audios"]) + len(request_payload["reference_videos"]),
                    "reference_image_count": len(request_payload["reference_images"]),
                    "reference_audio_count": len(request_payload["reference_audios"]),
                    "reference_video_count": len(request_payload["reference_videos"]),
                    "prompt_length": len(request_payload["prompt"]),
                }
                record_agent_video_generation_event(task, "started", generation_event_base)
                events.put({"type": "asset_agent.video_generation.started", "properties": generation_event_base})
                deps.add_event(int(task["session_id"]), "koubo_storyboard.asset_library_agent.video.agent_requested", {
                    "task_id": int(task["id"]),
                    **generation_event_base,
                })

                def worker() -> None:
                    started = time.time()
                    try:
                        result = deps.generate_asset_library_video(int(task["id"]), {
                            "title": request_payload["title"],
                            "prompt": request_payload["prompt"],
                            "duration": request_payload["duration"],
                            "aspect": request_payload["aspect"],
                            "reference_images": request_payload["reference_images"],
                            "reference_audios": request_payload["reference_audios"],
                            "reference_videos": request_payload["reference_videos"],
                            "reference_mode": request_payload["reference_mode"],
                            "provider": request_payload["provider"],
                            "model": request_payload["model"],
                            "agentVideoAlias": request_payload.get("agentVideoAlias") or "",
                            "chat_opencode_session_id": chat_session_id,
                            "agent_message_id": message_id,
                            "agent_generation_id": generation_key,
                            "client_action_id": _text(request_payload.get("client_action_id") or (request_context.get("settings") or {}).get("client_action_id")),
                            "operation": _text(request_payload.get("operation") or (request_context.get("settings") or {}).get("operation")),
                            "video_thread_id": _text(request_payload.get("video_thread_id") or (request_context.get("settings") or {}).get("video_thread_id")),
                            "parent_turn_id": _text(request_payload.get("parent_turn_id") or (request_context.get("settings") or {}).get("parent_turn_id")),
                            "stateful": True,
                        }, sc=deps)
                    except HTTPException as exc:
                        failed = {**generation_event_base, "status_code": exc.status_code, "detail": exc.detail, "elapsed_seconds": round(time.time() - started, 1)}
                        deps.add_event(int(task["session_id"]), "koubo_storyboard.asset_library_agent.video.agent_failed", failed)
                        record_agent_video_generation_event(task, "failed", failed)
                        events.put({"type": "asset_agent.video_generation.failed", "properties": failed})
                        return
                    except Exception as exc:
                        failed = {**generation_event_base, "detail": str(exc), "elapsed_seconds": round(time.time() - started, 1)}
                        deps.add_event(int(task["session_id"]), "koubo_storyboard.asset_library_agent.video.agent_failed", failed)
                        record_agent_video_generation_event(task, "failed", failed)
                        events.put({"type": "asset_agent.video_generation.failed", "properties": failed})
                        return
                    completed = {**generation_event_base, **result, "elapsed_seconds": round(time.time() - started, 1)}
                    record_agent_video_generation_event(task, "completed", completed)
                    events.put({"type": "asset_agent.video_generation.completed", "properties": completed})

                threading.Thread(target=worker, daemon=True).start()

        def enqueue_existing_agent_video_generations() -> None:
            if key != "asset_video":
                return
            try:
                messages = client.messages(chat_session_id, limit=KOUBO_AGENT_CHAT_MESSAGE_LIMIT)
            except Exception as exc:
                deps.add_event(int(task["session_id"]), "koubo_storyboard.asset_library_agent.video.catchup_failed", {
                    "task_id": int(task["id"]),
                    "chat_opencode_session_id": chat_session_id,
                    "detail": str(exc)[:1000],
                })
                return
            for message in messages if isinstance(messages, list) else []:
                generation_message = video_generation_message_from_event({
                    "type": "message.updated",
                    "properties": {"message": safe_opencode_message(message)},
                })
                if generation_message:
                    enqueue_agent_video_generation(generation_message)

        def enqueue_prompt_agent_critiques(message: dict[str, Any], retrieval_id: str | None = None, retrieval_match: dict[str, Any] | None = None) -> dict[str, Any] | None:
            if key != "prompt_agent":
                return None
            try:
                saved = write_prompt_agent_critique(task, message, retrieval_id, retrieval_match)
                if saved:
                    events.put({
                        "type": "prompt_agent.result.normalized",
                        "properties": {
                            "message_id": saved.get("message_id"),
                            "path": saved.get("path"),
                            "result": {
                                "mode": saved.get("mode"),
                                "summary": saved.get("summary"),
                                "issues": saved.get("issues") or [],
                                "revised_prompt": saved.get("revised_prompt") or "",
                                "negative_prompt": saved.get("negative_prompt") or "",
                                "changes": saved.get("changes") or [],
                                "model_notes": saved.get("model_notes") or [],
                                "used_sources": saved.get("used_sources") or [],
                                "source_validation": saved.get("source_validation") or {},
                                "retrieval_id": saved.get("retrieval_id") or "",
                                "retrieval_request_id": saved.get("retrieval_request_id") or "",
                            },
                        },
                    })
                return saved
            except Exception as exc:
                deps.add_event(int(task["session_id"]), "koubo_storyboard.prompt_agent.critique.save_failed", {
                    "task_id": int(task["id"]),
                    "session_id": int(task["session_id"]),
                    "detail": str(exc)[:1000],
                })
                return None

        def enqueue_existing_prompt_agent_critiques() -> None:
            if key != "prompt_agent":
                return
            try:
                messages = client.messages(chat_session_id, limit=KOUBO_AGENT_CHAT_MESSAGE_LIMIT)
            except Exception as exc:
                deps.add_event(int(task["session_id"]), "koubo_storyboard.prompt_agent.critique.catchup_failed", {
                    "task_id": int(task["id"]),
                    "chat_opencode_session_id": chat_session_id,
                    "detail": str(exc)[:1000],
                })
                return
            for message in messages if isinstance(messages, list) else []:
                completed_message = completed_assistant_message_from_event({
                    "type": "message.updated",
                    "properties": {"message": safe_opencode_message(message)},
                })
                if completed_message:
                    saved = enqueue_prompt_agent_critiques(completed_message, "", {"match": "backfill_unmatched"})
                    if saved:
                        expire_oldest_prompt_agent_pending_request(task, "backfill_unmatched", prompt_agent_message_completed_at(completed_message))

        def on_event(payload: dict[str, Any]) -> None:
            if opencode_event_has_tool_use(payload):
                try:
                    client.abort(chat_session_id)
                except Exception:
                    pass
                deps.add_event(int(task["session_id"]), "koubo_storyboard.agent_chat.tool_blocked", {
                    "task_id": int(task["id"]),
                    "session_id": int(task["session_id"]),
                    "agent_key": key,
                    "opencode_session_id": chat_session_id,
                    "event_type": _text(payload.get("type")),
                })
                events.put({"type": "koubo_agent.chat.tool_blocked", "properties": {"message": "OpenCode tool use was blocked for Koubo Agent chat."}})
                return
            sanitized = sanitize_opencode_event(payload)
            if sanitized:
                events.put(sanitized)
                generation_message = video_generation_message_from_event(sanitized)
                if generation_message:
                    enqueue_agent_video_generation(generation_message)
                completed_message = completed_assistant_message_from_event(sanitized)
                if completed_message:
                    enqueue_prompt_agent_critiques(completed_message)

        thread = threading.Thread(target=client.collect_events, args=(chat_session_id, stop_event, on_event), daemon=True)
        thread.start()
        enqueue_existing_agent_video_generations()
        enqueue_existing_prompt_agent_critiques()

        async def event_generator() -> Any:
            yield f"data: {json.dumps({'type': 'ready', 'agent_key': key, 'chat_opencode_session_id': chat_session_id}, ensure_ascii=True)}\n\n"
            heartbeat = 0
            last_catchup_at = time.time()
            try:
                while not await request.is_disconnected():
                    try:
                        item = events.get_nowait()
                    except queue.Empty:
                        if key == "asset_video" and time.time() - last_catchup_at >= 8:
                            last_catchup_at = time.time()
                            enqueue_existing_agent_video_generations()
                        if key == "prompt_agent" and time.time() - last_catchup_at >= 8:
                            last_catchup_at = time.time()
                            enqueue_existing_prompt_agent_critiques()
                        heartbeat += 1
                        if heartbeat % 15 == 0:
                            yield f"data: {json.dumps({'type': 'heartbeat', 'agent_key': key, 'chat_opencode_session_id': chat_session_id}, ensure_ascii=True)}\n\n"
                        await asyncio.sleep(1)
                        continue
                    yield f"data: {json.dumps(item, ensure_ascii=True)}\n\n"
            finally:
                stop_event.set()

        return StreamingResponse(event_generator(), media_type="text/event-stream")

    @router.post("/api/koubo-storyboard/tasks/{task_id}/agents/{agent_key}/chat/abort")
    async def abort_koubo_agent_chat(task_id: int, agent_key: str) -> dict[str, Any]:
        key = normalize_agent_key(agent_key)
        task = deps.task_or_404(task_id)
        state = read_agent_chat_state(task, key)
        chat_session_id = _text(state.get("opencode_session_id") or state.get("chat_opencode_session_id"))
        if not chat_session_id:
            return {"ok": True, "agent_key": key, "chat_opencode_session_id": ""}
        try:
            deps.opencode_client_for(agent_chat_session_row(task), sc=deps).abort(chat_session_id)
        except Exception as exc:
            if not opencode_session_not_found(exc):
                raise
            clear_stale_agent_chat_session(task, key, chat_session_id, "abort")
            return {"ok": True, "agent_key": key, "chat_opencode_session_id": "", "recovered": True}
        deps.add_event(int(task["session_id"]), "koubo_storyboard.agent_chat.aborted", {
            "task_id": int(task["id"]),
            "session_id": int(task["session_id"]),
            "agent_key": key,
            "opencode_session_id": chat_session_id,
        })
        return {"ok": True, "agent_key": key, "chat_opencode_session_id": chat_session_id}
