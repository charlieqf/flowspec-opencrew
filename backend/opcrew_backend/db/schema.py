from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Identity,
    Index,
    Integer,
    JSON,
    MetaData,
    Table,
    Text,
    UniqueConstraint,
    text,
)


metadata = MetaData()


app_settings = Table(
    "app_settings",
    metadata,
    Column("key", Text, primary_key=True),
    Column("value", Text, nullable=False),
    Column("updated_at", BigInteger, nullable=False),
)

opencode_runtime = Table(
    "opencode_runtime",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("status", Text, nullable=False),
    Column("base_url", Text),
    Column("health_url", Text),
    Column("auth_username", Text),
    Column("auth_password", Text),
    Column("auth_source", Text),
    Column("version", Text),
    Column("error", Text),
    Column("checked_at", BigInteger),
)

tunnel_runtime = Table(
    "tunnel_runtime",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("provider", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("command_path", Text),
    Column("public_url", Text),
    Column("webhook_url", Text),
    Column("pid", Integer),
    Column("error", Text),
    Column("updated_at", BigInteger),
)

npc_runtime = Table(
    "npc_runtime",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("environment_status", Text, nullable=False),
    Column("install_status", Text, nullable=False),
    Column("verify_status", Text, nullable=False),
    Column("platform", Text),
    Column("arch", Text),
    Column("brew_available", Integer, nullable=False, default=0),
    Column("npc_installed", Integer, nullable=False, default=0),
    Column("installed_by_opencrew", Integer, nullable=False, default=0),
    Column("available", Integer, nullable=False, default=0),
    Column("command_path", Text),
    Column("managed_path", Text),
    Column("version", Text),
    Column("install_method", Text),
    Column("last_task_id", Integer),
    Column("last_error", Text),
    Column("last_result", Text),
    Column("updated_at", BigInteger),
)

publish_runtime = Table(
    "publish_runtime",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("status", Text, nullable=False),
    Column("input_url", Text),
    Column("normalized_url", Text),
    Column("scheme", Text),
    Column("domain", Text),
    Column("path_prefix", Text),
    Column("nginx_config", Text),
    Column("nps_config", Text),
    Column("message", Text),
    Column("last_error", Text),
    Column("test_detail", Text),
    Column("updated_at", BigInteger),
    Column("tested_at", BigInteger),
)

npc_skills = Table(
    "npc_skills",
    metadata,
    Column("kind", Text, primary_key=True),
    Column("title", Text, nullable=False),
    Column("content", Text, nullable=False),
    Column("updated_at", BigInteger, nullable=False),
)

publish_skills = Table(
    "publish_skills",
    metadata,
    Column("kind", Text, primary_key=True),
    Column("title", Text, nullable=False),
    Column("content", Text, nullable=False),
    Column("updated_at", BigInteger, nullable=False),
)

openflow_skills = Table(
    "openflow_skills",
    metadata,
    Column("kind", Text, primary_key=True),
    Column("title", Text, nullable=False),
    Column("content", Text, nullable=False),
    Column("updated_at", BigInteger, nullable=False),
)

task_runs = Table(
    "task_runs",
    metadata,
    Column("id", Integer, Identity(), primary_key=True),
    Column("kind", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("session_id", Text),
    Column("summary", Text),
    Column("error", Text),
    Column("skill_snapshot", Text),
    Column("created_at", BigInteger, nullable=False),
    Column("started_at", BigInteger),
    Column("finished_at", BigInteger),
)

task_logs = Table(
    "task_logs",
    metadata,
    Column("id", Integer, Identity(), primary_key=True),
    Column("task_id", Integer, ForeignKey("task_runs.id"), nullable=False),
    Column("phase", Text, nullable=False),
    Column("level", Text, nullable=False),
    Column("message", Text, nullable=False),
    Column("created_at", BigInteger, nullable=False),
)

local_usage_log = Table(
    "local_usage_log",
    metadata,
    Column("id", BigInteger, Identity(), primary_key=True),
    Column("request_id", Text),
    Column("task_id", Text),
    Column("attempt_id", Text),
    Column("step_id", Text),
    Column("idempotency_key", Text),
    Column("provider", Text, nullable=False),
    Column("model_id", Text, nullable=False),
    Column("modality", Text, nullable=False),
    Column("provider_mode", Text, nullable=False, default="local_box"),
    Column("billing_mode", Text, nullable=False, default="local_usage_only"),
    Column("proxy_policy", Text),
    Column("status", Text, nullable=False),
    Column("units_json", JSON),
    Column("est_cost_micros", BigInteger),
    Column("actual_cost_micros", BigInteger),
    Column("actual_cost_currency", Text),
    Column("actual_cost_source", Text),
    Column("actual_cost_raw_json", JSON),
    Column("pricebook_version", Text),
    Column("billing_reconciled_at", BigInteger),
    Column("error_code", Text),
    Column("started_at", BigInteger),
    Column("finished_at", BigInteger),
    Column("created_at", BigInteger, nullable=False),
)
Index("ux_local_usage_log_idempotency_key", local_usage_log.c.idempotency_key, unique=True)
Index("ix_local_usage_log_task_attempt_step", local_usage_log.c.task_id, local_usage_log.c.attempt_id, local_usage_log.c.step_id)

wecom_config = Table(
    "wecom_config",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("corp_id", Text),
    Column("agent_id", Text),
    Column("secret", Text),
    Column("token", Text),
    Column("encoding_aes_key", Text),
    Column("enabled", Integer, nullable=False, default=0),
    Column("updated_at", BigInteger),
)

wecom_runtime = Table(
    "wecom_runtime",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("status", Text, nullable=False),
    Column("message", Text),
    Column("verified_at", BigInteger),
    Column("last_error", Text),
)

tool_asr_provider_configs = Table(
    "tool_asr_provider_configs",
    metadata,
    Column("id", Integer, Identity(), primary_key=True),
    Column("name", Text, nullable=False, unique=True),
    Column("provider", Text, nullable=False),
    Column("enabled", Boolean, nullable=False, default=True),
    Column("priority", Integer, nullable=False, default=100),
    Column("model", Text, nullable=False, default="small"),
    Column("language", Text, default="zh"),
    Column("api_url", Text),
    Column("api_key_ciphertext", Text),
    Column("api_key_ref", Text),
    Column("extra_json", Text, nullable=False, default="{}"),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

tool_media_provider_configs = Table(
    "tool_media_provider_configs",
    metadata,
    Column("id", Integer, Identity(), primary_key=True),
    Column("kind", Text, nullable=False),
    Column("provider", Text, nullable=False),
    Column("enabled", Boolean, nullable=False, default=True),
    Column("active", Boolean, nullable=False, default=False),
    Column("model", Text, nullable=False),
    Column("api_key_ciphertext", Text),
    Column("api_key_ref", Text),
    Column("extra_json", Text, nullable=False, default="{}"),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("kind", "provider", name="uq_tool_media_provider_kind_provider"),
)

message_logs = Table(
    "message_logs",
    metadata,
    Column("id", Integer, Identity(), primary_key=True),
    Column("source", Text, nullable=False),
    Column("external_id", Text),
    Column("sender", Text),
    Column("content", Text),
    Column("status", Text, nullable=False),
    Column("result", Text),
    Column("created_at", BigInteger, nullable=False),
)

verification_runs = Table(
    "verification_runs",
    metadata,
    Column("id", Integer, Identity(), primary_key=True),
    Column("status", Text, nullable=False),
    Column("message", Text),
    Column("detail", Text),
    Column("created_at", BigInteger, nullable=False),
)

event_logs = Table(
    "event_logs",
    metadata,
    Column("id", Integer, Identity(), primary_key=True),
    Column("level", Text, nullable=False),
    Column("category", Text, nullable=False),
    Column("message", Text, nullable=False),
    Column("payload", Text),
    Column("created_at", BigInteger, nullable=False),
)

sessions = Table(
    "sessions",
    metadata,
    Column("id", Integer, Identity(), primary_key=True),
    Column("source", Text, nullable=False),
    Column("group_id", Text, nullable=False),
    Column("sender_id", Text),
    Column("sender_name", Text),
    Column("title", Text, nullable=False),
    Column("command_text", Text),
    Column("status", Text, nullable=False),
    Column("opencode_session_id", Text),
    Column("workspace_dir", Text, nullable=False),
    Column("share_token", Text),
    Column("last_summary", Text),
    Column("created_at", BigInteger, nullable=False),
    Column("updated_at", BigInteger, nullable=False),
    Column("started_at", BigInteger),
    Column("finished_at", BigInteger),
)

session_events = Table(
    "session_events",
    metadata,
    Column("id", Integer, Identity(), primary_key=True),
    Column("session_id", Integer, ForeignKey("sessions.id"), nullable=False),
    Column("kind", Text, nullable=False),
    Column("payload", Text),
    Column("visibility", Text),
    Column("event_scope", Text),
    Column("severity", Text),
    Column("family", Text),
    Column("workflow_id", Text),
    Column("task_id", Integer),
    Column("attempt_id", Integer),
    Column("tool_id", Text),
    Column("step_id", Text),
    Column("created_at", BigInteger, nullable=False),
)

session_files = Table(
    "session_files",
    metadata,
    Column("id", Integer, Identity(), primary_key=True),
    Column("session_id", Integer, ForeignKey("sessions.id"), nullable=False),
    Column("path", Text, nullable=False),
    Column("kind", Text, nullable=False),
    Column("size", BigInteger, nullable=False, default=0),
    Column("origin", Text, nullable=False),
    Column("downloadable", Integer, nullable=False, default=1),
    Column("visibility", Text),
    Column("sensitivity", Text),
    Column("attempt_id", Integer),
    Column("tool_use_session_id", Text),
    Column("stale", Integer, nullable=False, default=0),
    Column("updated_at", BigInteger, nullable=False),
    UniqueConstraint("session_id", "path", name="uq_session_files_session_path"),
)

video_interaction_threads = Table(
    "video_interaction_threads",
    metadata,
    Column("thread_id", Text, primary_key=True),
    Column("task_id", BigInteger, nullable=False),
    Column("session_id", Integer, ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False),
    Column("actor_id", Text, nullable=False),
    Column("chat_session_id", Text),
    Column("model_alias", Text, nullable=False),
    Column("internal_provider", Text, nullable=False),
    Column("internal_model", Text, nullable=False),
    Column("head_turn_id", Text),
    Column("status", Text, nullable=False, default="active"),
    Column("lease_token", Text),
    Column("lease_expires_at", BigInteger),
    Column("row_version", BigInteger, nullable=False, default=0),
    Column("created_at", BigInteger, nullable=False),
    Column("updated_at", BigInteger, nullable=False),
    UniqueConstraint("thread_id", "task_id", "actor_id", name="uq_video_interaction_thread_scope"),
)
Index("ix_video_interaction_threads_task_actor", video_interaction_threads.c.task_id, video_interaction_threads.c.actor_id)
Index("ix_video_interaction_threads_lease", video_interaction_threads.c.status, video_interaction_threads.c.lease_expires_at)

video_interaction_turns = Table(
    "video_interaction_turns",
    metadata,
    Column("turn_id", Text, primary_key=True),
    Column("thread_id", Text, ForeignKey("video_interaction_threads.thread_id", ondelete="CASCADE"), nullable=False),
    Column("task_id", BigInteger, nullable=False),
    Column("actor_id", Text, nullable=False),
    Column("parent_turn_id", Text, ForeignKey("video_interaction_turns.turn_id", ondelete="SET NULL")),
    Column("client_action_id", Text, nullable=False),
    Column("client_action_scope", Text, nullable=False),
    Column("request_config_json", JSON),
    Column("usage_request_id", Text),
    Column("local_usage_id", Text),
    Column("interaction_id", Text),
    Column("operation", Text, nullable=False),
    Column("prompt", Text, nullable=False),
    Column("input_asset_id", Text),
    Column("output_asset_id", Text),
    Column("output_path", Text),
    Column("status", Text, nullable=False),
    Column("provider_request_status", Text, nullable=False, default="not_sent"),
    Column("provider_state_status", Text, nullable=False, default="pending"),
    Column("provider_state_expires_at", BigInteger),
    Column("provider_expiry_source", Text, nullable=False, default="unknown"),
    Column("delete_status", Text, nullable=False, default="not_requested"),
    Column("delete_attempts", Integer, nullable=False, default=0),
    Column("delete_error", Text),
    Column("expected_head_turn_id", Text),
    Column("expected_row_version", BigInteger, nullable=False, default=0),
    Column("created_at", BigInteger, nullable=False),
    Column("updated_at", BigInteger, nullable=False),
    UniqueConstraint(
        "task_id",
        "actor_id",
        "operation",
        "client_action_id",
        name="uq_video_interaction_turn_action_operation",
    ),
    UniqueConstraint(
        "task_id",
        "actor_id",
        "client_action_id",
        name="uq_video_interaction_turn_action",
    ),
    UniqueConstraint("usage_request_id", name="uq_video_interaction_turn_usage_request"),
)
Index("ix_video_interaction_turns_thread_created", video_interaction_turns.c.thread_id, video_interaction_turns.c.created_at)
Index("ix_video_interaction_turns_parent", video_interaction_turns.c.parent_turn_id)
Index("ix_video_interaction_turns_pending", video_interaction_turns.c.status, video_interaction_turns.c.updated_at)
Index("ix_video_interaction_turns_delete", video_interaction_turns.c.delete_status, video_interaction_turns.c.updated_at)

media_library_assets = Table(
    "media_library_assets",
    metadata,
    Column("asset_id", Text, primary_key=True),
    Column("session_id", Integer, ForeignKey("sessions.id"), unique=True),
    Column("display_name", Text, nullable=False),
    Column("original_filename", Text, nullable=False),
    Column("source_video_path", Text),
    Column("content_sha256", Text),
    Column("content_hashed_at", BigInteger),
    Column("media_type", Text, nullable=False, default="video"),
    Column("thumbnail_url", Text),
    Column("preview_url", Text),
    Column("duration_ms", BigInteger),
    Column("width", Integer),
    Column("height", Integer),
    Column("format", Text),
    Column("size_bytes", BigInteger),
    Column("language", Text),
    Column("dialogue_summary", Text),
    Column("upload_status", Text, nullable=False, default="ready"),
    Column("analysis_status", Text, nullable=False, default="not_analyzed"),
    Column("subtitle_mode", Text, nullable=False, default="unknown"),
    Column("analysis_summary_json", JSON),
    Column("tags_json", JSON),
    Column("archived", Boolean, nullable=False, default=False),
    Column("referenced_by_count", Integer, nullable=False, default=0),
    Column("created_at", BigInteger, nullable=False),
    Column("updated_at", BigInteger, nullable=False),
)

media_library_tasks = Table(
    "media_library_tasks",
    metadata,
    Column("id", Integer, Identity(), primary_key=True),
    Column("asset_id", Text, ForeignKey("media_library_assets.asset_id", ondelete="CASCADE"), nullable=False, unique=True),
    Column("session_id", Integer, ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, unique=True),
    Column("title", Text, nullable=False),
    Column("status", Text, nullable=False, default="draft"),
    Column("dialogue_status", Text, nullable=False, default="not_analyzed"),
    Column("dialogue_current_run_id", Text),
    Column("dialogue_tool_use_session_id", Text),
    Column("dialogue_error", Text),
    Column("dialogue_progress_json", JSON),
    Column("visual_status", Text, nullable=False, default="not_analyzed"),
    Column("visual_structure_status", Text, nullable=False, default="not_analyzed"),
    Column("visual_structure_current_run_id", Text),
    Column("visual_tool_use_session_id", Text),
    Column("visual_error", Text),
    Column("visual_progress_json", JSON),
    Column("visual_semantic_status", Text, nullable=False, default="not_analyzed"),
    Column("visual_semantic_current_run_id", Text),
    Column("visual_semantic_tool_use_session_id", Text),
    Column("visual_semantic_error", Text),
    Column("visual_semantic_progress_json", JSON),
    Column("composite_status", Text, nullable=False, default="not_analyzed"),
    Column("composite_current_run_id", Text),
    Column("composite_tool_use_session_id", Text),
    Column("composite_error", Text),
    Column("composite_progress_json", JSON),
    Column("created_at", BigInteger, nullable=False),
    Column("updated_at", BigInteger, nullable=False),
)

media_library_analysis_runs = Table(
    "media_library_analysis_runs",
    metadata,
    Column("analysis_run_id", Text, primary_key=True),
    Column(
        "asset_id",
        Text,
        ForeignKey("media_library_assets.asset_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("scheme", Text, nullable=False),
    Column("source_version", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("tool_use_session_id", Text),
    Column("attempt_id", BigInteger),
    Column("prompt_version", Text),
    Column("model_config_id", Text),
    Column("model_session_id", Text),
    Column("schema_version", Text),
    Column("result_hash", Text),
    Column("result_index_path", Text),
    Column("upstream_refs_json", JSON),
    Column("progress_json", JSON),
    Column("error_code", Text),
    Column("error_json", JSON),
    Column("is_current", Boolean, nullable=False, default=False),
    Column("started_at", BigInteger),
    Column("finished_at", BigInteger),
    Column("created_at", BigInteger, nullable=False),
    Column("updated_at", BigInteger, nullable=False),
    CheckConstraint(
        "scheme IN ('dialogue', 'visual_structure', 'visual_semantic', 'composite')",
        name="ck_media_library_analysis_runs_scheme",
    ),
    CheckConstraint(
        "status IN ('queued', 'running', 'blocked', 'ready', 'stale', 'failed')",
        name="ck_media_library_analysis_runs_status",
    ),
)
Index(
    "ux_media_library_analysis_runs_current",
    media_library_analysis_runs.c.asset_id,
    media_library_analysis_runs.c.scheme,
    unique=True,
    postgresql_where=media_library_analysis_runs.c.is_current.is_(True),
    sqlite_where=media_library_analysis_runs.c.is_current.is_(True),
)
Index(
    "ux_media_library_analysis_runs_tool_session",
    media_library_analysis_runs.c.tool_use_session_id,
    unique=True,
    postgresql_where=media_library_analysis_runs.c.tool_use_session_id.is_not(None),
    sqlite_where=media_library_analysis_runs.c.tool_use_session_id.is_not(None),
)
Index(
    "ix_media_library_analysis_runs_asset_scheme_created",
    media_library_analysis_runs.c.asset_id,
    media_library_analysis_runs.c.scheme,
    media_library_analysis_runs.c.created_at.desc(),
)
Index(
    "ux_media_library_analysis_runs_one_active",
    media_library_analysis_runs.c.asset_id,
    media_library_analysis_runs.c.scheme,
    unique=True,
    postgresql_where=media_library_analysis_runs.c.status.in_(("queued", "running")),
    sqlite_where=media_library_analysis_runs.c.status.in_(("queued", "running")),
)

sqlite_bigint_identity = BigInteger().with_variant(Integer, "sqlite")

media_library_fragment_index = Table(
    "media_library_fragment_index",
    metadata,
    Column("id", sqlite_bigint_identity, Identity(), primary_key=True),
    Column(
        "asset_id",
        Text,
        ForeignKey("media_library_assets.asset_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column(
        "source_session_id",
        BigInteger,
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("source_version", Text, nullable=False),
    Column("analysis_scheme", Text, nullable=False),
    Column(
        "analysis_run_id",
        Text,
        ForeignKey(
            "media_library_analysis_runs.analysis_run_id", ondelete="CASCADE"
        ),
        nullable=False,
    ),
    Column("result_hash", Text, nullable=False),
    Column("fragment_id", Text, nullable=False),
    Column("start_ms", BigInteger, nullable=False),
    Column("end_ms", BigInteger, nullable=False),
    Column("dialogue_text", Text),
    Column("title", Text),
    Column("summary", Text),
    Column("keywords_json", JSON, nullable=False, default=list),
    Column("visual_labels_json", JSON, nullable=False, default=list),
    Column("keyframe_ref_json", JSON),
    Column("search_text", Text, nullable=False),
    Column("search_lexemes_text", Text),
    Column(
        "tokenizer_name",
        Text,
        nullable=False,
        default="none",
        server_default=text("'none'"),
    ),
    Column(
        "tokenizer_version",
        Text,
        nullable=False,
        default="none",
        server_default=text("'none'"),
    ),
    Column("dictionary_hash", Text),
    Column(
        "normalization_version",
        Text,
        nullable=False,
        default="nfkc_casefold_ws_v1",
        server_default=text("'nfkc_casefold_ws_v1'"),
    ),
    Column(
        "quality_status",
        Text,
        nullable=False,
        default="ready",
        server_default=text("'ready'"),
    ),
    Column("confidence", Float),
    Column(
        "is_active",
        Boolean,
        nullable=False,
        default=False,
        server_default=text("FALSE"),
    ),
    Column("created_at", BigInteger, nullable=False),
    Column("updated_at", BigInteger, nullable=False),
    UniqueConstraint(
        "analysis_run_id",
        "fragment_id",
        name="uq_media_library_fragment_run_fragment",
    ),
    CheckConstraint(
        "analysis_scheme IN ('dialogue', 'visual_semantic', 'composite')",
        name="ck_media_library_fragment_scheme",
    ),
    CheckConstraint(
        "start_ms >= 0 AND end_ms > start_ms",
        name="ck_media_library_fragment_time_range",
    ),
    CheckConstraint(
        "quality_status IN ('ready', 'review')",
        name="ck_media_library_fragment_quality",
    ),
    CheckConstraint(
        "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
        name="ck_media_library_fragment_confidence",
    ),
    sqlite_autoincrement=True,
)
Index(
    "ix_media_library_fragment_active_scheme_asset",
    media_library_fragment_index.c.is_active,
    media_library_fragment_index.c.analysis_scheme,
    media_library_fragment_index.c.asset_id,
)
Index(
    "ix_media_library_fragment_asset_scheme_active",
    media_library_fragment_index.c.asset_id,
    media_library_fragment_index.c.analysis_scheme,
    media_library_fragment_index.c.is_active,
)
Index(
    "ix_media_library_fragment_analysis_run",
    media_library_fragment_index.c.analysis_run_id,
)

media_library_search_runs = Table(
    "media_library_search_runs",
    metadata,
    Column("search_id", Text, primary_key=True),
    Column("entry_point", Text, nullable=False),
    Column("target_task_id", BigInteger),
    Column("dialogue_asset_key", Text),
    Column("source_asset_id", Text),
    Column("query_source", Text, nullable=False),
    Column("query_hash", Text, nullable=False),
    Column("query_plan_json", JSON, nullable=False),
    Column("planner_version", Text, nullable=False),
    Column("retrieval_version", Text, nullable=False),
    Column("planner_degraded", Boolean, nullable=False),
    Column("requested_sources_json", JSON, nullable=False),
    Column("source_runs_json", JSON, nullable=False),
    Column("status", Text, nullable=False),
    Column("result_count", Integer, nullable=False, default=0),
    Column("zero_result", Boolean, nullable=False, default=True),
    Column("planner_latency_ms", BigInteger),
    Column("retrieval_latency_ms", BigInteger),
    Column("total_latency_ms", BigInteger),
    Column("top_candidates_json", JSON, nullable=False),
    Column("error_code", Text),
    Column("created_at", BigInteger, nullable=False),
    Column("updated_at", BigInteger, nullable=False),
    CheckConstraint(
        "entry_point IN ('storyboard', 'agent', 'editor')",
        name="ck_media_library_search_entry_point",
    ),
    CheckConstraint(
        "query_source IN ('dialogue', 'manual', 'planner')",
        name="ck_media_library_search_query_source",
    ),
    CheckConstraint(
        "status IN ('queued', 'running', 'completed', 'failed')",
        name="ck_media_library_search_status",
    ),
)

media_library_search_actions = Table(
    "media_library_search_actions",
    metadata,
    Column("id", sqlite_bigint_identity, Identity(), primary_key=True),
    Column(
        "search_id",
        Text,
        ForeignKey("media_library_search_runs.search_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("action_kind", Text, nullable=False),
    Column("source", Text, nullable=False),
    Column("candidate_id", Text, nullable=False),
    Column("source_asset_id", Text),
    Column("candidate_rank", Integer),
    Column("target_task_id", BigInteger),
    Column("metadata_json", JSON, nullable=False),
    Column("created_at", BigInteger, nullable=False),
    CheckConstraint(
        "action_kind IN ('preview', 'open_editor', 'import')",
        name="ck_media_library_search_action_kind",
    ),
)
Index(
    "ix_media_library_search_action_search_created",
    media_library_search_actions.c.search_id,
    media_library_search_actions.c.created_at,
)
Index(
    "ix_media_library_search_action_kind_created",
    media_library_search_actions.c.action_kind,
    media_library_search_actions.c.created_at,
)
Index(
    "ix_media_library_search_action_target_created",
    media_library_search_actions.c.target_task_id,
    media_library_search_actions.c.created_at,
)

media_library_clip_derivatives = Table(
    "media_library_clip_derivatives",
    metadata,
    Column("clip_id", Text, primary_key=True),
    Column("idempotency_key", Text, nullable=False, unique=True),
    Column(
        "source_asset_id",
        Text,
        ForeignKey("media_library_assets.asset_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column(
        "source_session_id",
        BigInteger,
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("source_version", Text, nullable=False),
    Column("source_start_ms", BigInteger, nullable=False),
    Column("source_end_ms", BigInteger, nullable=False),
    Column("source_scheme", Text),
    Column("source_fragment_id", Text),
    Column("source_analysis_run_id", Text),
    Column("source_search_id", Text),
    Column("source_dialogue_asset_key", Text),
    Column("output_path", Text, nullable=False),
    Column("display_name", Text, nullable=False),
    Column("duration_ms", BigInteger, nullable=False),
    Column("content_sha256", Text, nullable=False),
    Column("size_bytes", BigInteger, nullable=False),
    Column(
        "operation",
        Text,
        nullable=False,
        default="precise_reencode_v1",
        server_default=text("'precise_reencode_v1'"),
    ),
    Column(
        "search_eligible",
        Boolean,
        nullable=False,
        default=False,
        server_default=text("FALSE"),
    ),
    Column(
        "tags_json",
        JSON,
        nullable=False,
        default=list,
        server_default=text("'[]'"),
    ),
    Column("search_text", Text, nullable=False, default="", server_default=text("''")),
    Column(
        "search_normalization_version",
        Text,
        nullable=False,
        default="nfkc_casefold_ws_v1",
        server_default=text("'nfkc_casefold_ws_v1'"),
    ),
    Column("search_enabled_at", BigInteger),
    Column("search_updated_at", BigInteger),
    Column("created_at", BigInteger, nullable=False),
    UniqueConstraint(
        "source_session_id",
        "output_path",
        name="uq_media_library_clip_session_output",
    ),
    CheckConstraint(
        "source_start_ms >= 0 AND source_end_ms > source_start_ms",
        name="ck_media_library_clip_source_range",
    ),
    CheckConstraint(
        "duration_ms > 0", name="ck_media_library_clip_duration"
    ),
    CheckConstraint(
        "output_path <> '' AND output_path NOT LIKE '/%' "
        "AND output_path NOT LIKE '../%' AND output_path NOT LIKE '%/../%'",
        name="ck_media_library_clip_output_path",
    ),
)
Index(
    "ix_media_library_clip_search_eligible_source",
    media_library_clip_derivatives.c.search_eligible,
    media_library_clip_derivatives.c.source_asset_id,
    media_library_clip_derivatives.c.created_at,
)

media_library_storyboard_imports = Table(
    "media_library_storyboard_imports",
    metadata,
    Column("import_id", Text, primary_key=True),
    Column("idempotency_key", Text, nullable=False, unique=True),
    Column("source_kind", Text, nullable=False),
    Column(
        "source_asset_id",
        Text,
        ForeignKey("media_library_assets.asset_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column(
        "source_clip_id",
        Text,
        ForeignKey("media_library_clip_derivatives.clip_id"),
    ),
    Column("source_version", Text, nullable=False),
    Column(
        "source_search_id",
        Text,
        ForeignKey("media_library_search_runs.search_id", ondelete="SET NULL"),
    ),
    Column("source_dialogue_asset_key", Text),
    Column(
        "target_task_id",
        BigInteger,
        ForeignKey("openclip_tasks.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column(
        "target_session_id",
        BigInteger,
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("target_path", Text, nullable=False),
    Column("target_manifest_asset_id", Text, nullable=False),
    Column("content_sha256", Text, nullable=False),
    Column("size_bytes", BigInteger, nullable=False),
    Column("requested_name", Text),
    Column("status", Text, nullable=False),
    Column("error_code", Text),
    Column("created_at", BigInteger, nullable=False),
    Column("updated_at", BigInteger, nullable=False),
    UniqueConstraint(
        "target_session_id",
        "target_path",
        name="uq_media_library_storyboard_import_target_path",
    ),
    CheckConstraint(
        "source_kind IN ('media_library_original', 'media_library_clip')",
        name="ck_media_library_storyboard_import_source_kind",
    ),
    CheckConstraint(
        "status IN ('preparing', 'completed', 'failed')",
        name="ck_media_library_storyboard_import_status",
    ),
    CheckConstraint(
        "target_path <> '' AND target_path NOT LIKE '/%' "
        "AND target_path NOT LIKE '../%' AND target_path NOT LIKE '%/../%'",
        name="ck_media_library_storyboard_import_target_path",
    ),
)

media_library_uploads = Table(
    "media_library_uploads",
    metadata,
    Column("upload_id", Text, primary_key=True),
    Column("asset_id", Text, ForeignKey("media_library_assets.asset_id", ondelete="CASCADE"), nullable=False, unique=True),
    Column("session_id", Integer, ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, unique=True),
    Column("filename", Text, nullable=False),
    Column("safe_filename", Text, nullable=False),
    Column("content_type", Text),
    Column("size_bytes", BigInteger, nullable=False),
    Column("chunk_size", BigInteger, nullable=False),
    Column("total_chunks", Integer, nullable=False),
    Column("received_chunks_json", JSON, nullable=False, default=list),
    Column("received_bytes", BigInteger, nullable=False, default=0),
    Column("status", Text, nullable=False),
    Column("error", Text),
    Column("finalization_token", Text),
    Column("finalization_started_at", BigInteger),
    Column("created_at", BigInteger, nullable=False),
    Column("updated_at", BigInteger, nullable=False),
    Column("expires_at", BigInteger, nullable=False),
)

session_shares = Table(
    "session_shares",
    metadata,
    Column("id", Integer, Identity(), primary_key=True),
    Column("session_id", Integer, ForeignKey("sessions.id"), nullable=False),
    Column("token", Text, nullable=False),
    Column("scope", Text, nullable=False),
    Column("expires_at", BigInteger, nullable=False),
    Column("created_at", BigInteger, nullable=False),
    UniqueConstraint("token", name="uq_session_shares_token"),
)

workflow_plans = Table(
    "workflow_plans",
    metadata,
    Column("id", Integer, Identity(), primary_key=True),
    Column("workflow_id", Text, nullable=False),
    Column("task_id", Integer, nullable=False),
    Column("session_id", Integer, ForeignKey("sessions.id"), nullable=False),
    Column("status", Text, nullable=False),
    Column("plan_json", Text, nullable=False),
    Column("created_by_message_id", Text),
    Column("confirmed_by_message_id", Text),
    Column("created_at", BigInteger, nullable=False),
    Column("updated_at", BigInteger, nullable=False),
    Column("confirmed_at", BigInteger),
)

openflow_analysis_runs = Table(
    "openflow_analysis_runs",
    metadata,
    Column("id", Integer, Identity(), primary_key=True),
    Column("session_id", Integer, ForeignKey("sessions.id"), nullable=False),
    Column("reference_video_path", Text),
    Column("industry", Text),
    Column("persona", Text),
    Column("target_audience", Text),
    Column("product_info", Text),
    Column("constraints", Text),
    Column("analysis_goal", Text),
    Column("video_formula", Text),
    Column("simple_prompt", Text),
    Column("final_prompt", Text),
    Column("prompt_model_provider", Text),
    Column("prompt_model_id", Text),
    Column("generated_skill_content", Text),
    Column("skill_model_provider", Text),
    Column("skill_model_id", Text),
    Column("skill_version_name", Text),
    Column("skill_version_notes", Text),
    Column("skill_versions_json", Text),
    Column("version_name", Text),
    Column("version_notes", Text),
    Column("versions_json", Text),
    Column("created_at", BigInteger, nullable=False),
    Column("updated_at", BigInteger, nullable=False),
    UniqueConstraint("session_id", name="uq_openflow_analysis_runs_session_id"),
)

openclip_tasks = Table(
    "openclip_tasks",
    metadata,
    Column("id", Integer, Identity(), primary_key=True),
    Column("session_id", Integer, ForeignKey("sessions.id"), nullable=False),
    Column("status", Text, nullable=False),
    Column("workflow_mode", Text),
    Column("reference_video_path", Text),
    Column("industry", Text),
    Column("persona", Text),
    Column("target_audience", Text),
    Column("product_info", Text),
    Column("constraints", Text),
    Column("analysis_goal", Text),
    Column("video_formula", Text),
    Column("simple_prompt", Text),
    Column("final_prompt", Text),
    Column("rewrite_simple_prompt", Text),
    Column("rewrite_final_prompt", Text),
    Column("storyboard_simple_prompt", Text),
    Column("storyboard_final_prompt", Text),
    Column("storyboard_quick_config_json", Text),
    Column("current_prompt_version_id", Integer),
    Column("current_skill_version_id", Integer),
    Column("latest_attempt_id", Integer),
    Column("prompt_model_provider", Text),
    Column("prompt_model_id", Text),
    Column("generated_skill_content", Text),
    Column("skill_version_name", Text),
    Column("skill_version_notes", Text),
    Column("run_model_provider", Text),
    Column("run_model_id", Text),
    Column("created_at", BigInteger, nullable=False),
    Column("updated_at", BigInteger, nullable=False),
    UniqueConstraint("session_id", name="uq_openclip_tasks_session_id"),
)

talking_head_task_configs = Table(
    "talking_head_task_configs",
    metadata,
    Column("task_id", Integer, ForeignKey("openclip_tasks.id"), primary_key=True),
    Column("schema_version", Text, nullable=False),
    Column("script_creation_mode", Text, nullable=False),
    Column("config_json", Text, nullable=False),
    Column("created_at", BigInteger, nullable=False),
    Column("updated_at", BigInteger, nullable=False),
)

openclip_prompt_versions = Table(
    "openclip_prompt_versions",
    metadata,
    Column("id", Integer, Identity(), primary_key=True),
    Column("task_id", Integer, ForeignKey("openclip_tasks.id"), nullable=False),
    Column("name", Text, nullable=False),
    Column("notes", Text),
    Column("reference_video_path", Text),
    Column("industry", Text),
    Column("persona", Text),
    Column("target_audience", Text),
    Column("product_info", Text),
    Column("constraints", Text),
    Column("analysis_goal", Text),
    Column("video_formula", Text),
    Column("simple_prompt", Text),
    Column("rewrite_simple_prompt", Text),
    Column("rewrite_final_prompt", Text),
    Column("storyboard_simple_prompt", Text),
    Column("storyboard_final_prompt", Text),
    Column("storyboard_quick_config_json", Text),
    Column("prompt_model_provider", Text),
    Column("prompt_model_id", Text),
    Column("final_prompt", Text, nullable=False),
    Column("created_at", BigInteger, nullable=False),
)

openclip_skill_versions = Table(
    "openclip_skill_versions",
    metadata,
    Column("id", Integer, Identity(), primary_key=True),
    Column("task_id", Integer, ForeignKey("openclip_tasks.id"), nullable=False),
    Column("prompt_version_id", Integer, ForeignKey("openclip_prompt_versions.id")),
    Column("name", Text, nullable=False),
    Column("notes", Text),
    Column("skill_model_provider", Text),
    Column("skill_model_id", Text),
    Column("skill_content", Text, nullable=False),
    Column("created_at", BigInteger, nullable=False),
)

openclip_attempts = Table(
    "openclip_attempts",
    metadata,
    Column("id", Integer, Identity(), primary_key=True),
    Column("task_id", Integer, ForeignKey("openclip_tasks.id"), nullable=False),
    Column("session_id", Integer, ForeignKey("sessions.id"), nullable=False),
    Column("attempt_no", Integer, nullable=False),
    Column("status", Text, nullable=False),
    Column("prompt_version_id", Integer, ForeignKey("openclip_prompt_versions.id")),
    Column("skill_version_id", Integer, ForeignKey("openclip_skill_versions.id")),
    Column("run_model_provider", Text),
    Column("run_model_id", Text),
    Column("summary", Text),
    Column("result_manifest_json", Text),
    Column("tool_use_session_id", Text),
    Column("started_at", BigInteger),
    Column("finished_at", BigInteger),
    Column("created_at", BigInteger, nullable=False),
    UniqueConstraint("task_id", "attempt_no", name="ux_openclip_attempts_task_id_attempt_no"),
)

oc_rebuild_tasks = Table(
    "oc_rebuild_tasks",
    metadata,
    Column("id", Integer, Identity(), primary_key=True),
    Column("session_id", Integer, ForeignKey("sessions.id"), nullable=False),
    Column("analysis_task_id", Integer, ForeignKey("openclip_tasks.id")),
    Column("status", Text, nullable=False),
    Column("source_package_path", Text),
    Column("source_scheme", Text),
    Column("target_topic", Text),
    Column("target_platform", Text),
    Column("aspect_ratio", Text),
    Column("target_count", Integer),
    Column("target_audience", Text),
    Column("product_info", Text),
    Column("rebuild_goal", Text),
    Column("preserve_strategy_json", Text),
    Column("replace_strategy_json", Text),
    Column("visual_style", Text),
    Column("subtitle_style", Text),
    Column("title_style", Text),
    Column("voice_style", Text),
    Column("batch_variables", Text),
    Column("constraints", Text),
    Column("simple_prompt", Text),
    Column("final_prompt", Text),
    Column("current_version_id", Integer),
    Column("latest_attempt_id", Integer),
    Column("prompt_model_provider", Text),
    Column("prompt_model_id", Text),
    Column("run_model_provider", Text),
    Column("run_model_id", Text),
    Column("workflow_mode", Text),
    Column("created_at", BigInteger, nullable=False),
    Column("updated_at", BigInteger, nullable=False),
    UniqueConstraint("session_id", name="uq_oc_rebuild_tasks_session_id"),
)

oc_rebuild_prompt_versions = Table(
    "oc_rebuild_prompt_versions",
    metadata,
    Column("id", Integer, Identity(), primary_key=True),
    Column("task_id", Integer, ForeignKey("oc_rebuild_tasks.id"), nullable=False),
    Column("name", Text, nullable=False),
    Column("notes", Text),
    Column("snapshot_json", Text, nullable=False),
    Column("final_prompt", Text, nullable=False),
    Column("created_at", BigInteger, nullable=False),
)

oc_rebuild_attempts = Table(
    "oc_rebuild_attempts",
    metadata,
    Column("id", Integer, Identity(), primary_key=True),
    Column("task_id", Integer, ForeignKey("oc_rebuild_tasks.id"), nullable=False),
    Column("session_id", Integer, ForeignKey("sessions.id"), nullable=False),
    Column("attempt_no", Integer, nullable=False),
    Column("status", Text, nullable=False),
    Column("run_model_provider", Text),
    Column("run_model_id", Text),
    Column("summary", Text),
    Column("result_manifest_json", Text),
    Column("tool_use_session_id", Text),
    Column("started_at", BigInteger),
    Column("finished_at", BigInteger),
    Column("created_at", BigInteger, nullable=False),
    UniqueConstraint("task_id", "attempt_no", name="ux_oc_rebuild_attempts_task_id_attempt_no"),
)


Index("ix_task_logs_task_id", task_logs.c.task_id)
Index("ix_event_logs_created_at", event_logs.c.created_at)
Index("ix_sessions_group_id_updated", sessions.c.group_id, sessions.c.updated_at)
Index("ix_session_events_session_id_id", session_events.c.session_id, session_events.c.id)
Index("ix_session_files_session_id", session_files.c.session_id)
Index("ix_media_library_assets_updated", media_library_assets.c.updated_at)
Index("ix_media_library_assets_status", media_library_assets.c.analysis_status, media_library_assets.c.archived)
Index("ix_media_library_assets_upload_status", media_library_assets.c.upload_status, media_library_assets.c.updated_at)
Index("ix_media_library_tasks_status", media_library_tasks.c.status, media_library_tasks.c.updated_at)
Index("ix_media_library_uploads_status_expiry", media_library_uploads.c.status, media_library_uploads.c.expires_at)
Index("ix_session_shares_session_id", session_shares.c.session_id)
Index("ix_workflow_plans_workflow_task", workflow_plans.c.workflow_id, workflow_plans.c.task_id)
Index("ix_workflow_plans_session_id", workflow_plans.c.session_id)
Index("ix_openflow_analysis_runs_session_id", openflow_analysis_runs.c.session_id)
Index("ix_openclip_tasks_session_id", openclip_tasks.c.session_id)
Index("ix_openclip_prompt_versions_task_id", openclip_prompt_versions.c.task_id)
Index("ix_openclip_skill_versions_task_id", openclip_skill_versions.c.task_id)
Index("ix_openclip_attempts_task_id", openclip_attempts.c.task_id)
Index("ux_openclip_attempts_tool_use_session_id", openclip_attempts.c.tool_use_session_id, unique=True)
Index("ix_oc_rebuild_tasks_session_id", oc_rebuild_tasks.c.session_id)
Index("ix_oc_rebuild_tasks_analysis_task_id", oc_rebuild_tasks.c.analysis_task_id)
Index("ix_oc_rebuild_prompt_versions_task_id", oc_rebuild_prompt_versions.c.task_id)
Index("ix_oc_rebuild_attempts_task_id", oc_rebuild_attempts.c.task_id)
Index("ux_oc_rebuild_attempts_tool_use_session_id", oc_rebuild_attempts.c.tool_use_session_id, unique=True)
