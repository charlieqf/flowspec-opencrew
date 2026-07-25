// Canonical frontend API wrapper. New API calls should be added here; legacy module helpers are frozen by scripts/check_frontend_api_helper_freeze.py.
export type SummaryPayload = {
  summary: {
    opencode: Record<string, string | number | null>;
    npc: Record<string, string | number | null>;
    tunnel: Record<string, string | number | null>;
    publish: Record<string, string | number | null>;
    wecom: Record<string, string | number | null>;
    sessions: Record<string, string | number | null>;
    verification: Record<string, string | number | null>;
  };
  events: Array<{
    id: number;
    level: string;
    category: string;
    message: string;
    payload: string | null;
    created_at: number;
  }>;
};

export type OpenCodeCandidate = {
  process: string;
  pid: number | null;
  listen: string;
  host: string;
  port: number;
  base_url: string;
  source: string;
  healthy: boolean;
  version: string | null;
  probe_status: string;
  http_status: number | null;
  username: string | null;
  password: string | null;
  auth_source: string | null;
  error: string | null;
};

export type OpenCodeDiscoverPayload = {
  ok: boolean;
  system: string;
  selected: OpenCodeCandidate | null;
  candidates: OpenCodeCandidate[];
};

export type ASRModelOption = {
  provider: string;
  model: string;
  label: string;
  description: string;
  api_url: string;
};

export type ASRConfigPayload = {
  config_name: string;
  provider: string;
  model: string;
  language: string;
  api_url: string;
  enabled: boolean;
  has_api_key: boolean;
  api_key_ref: string;
  updated_at: number | null;
};

export type ASRConfigResponse = {
  config: ASRConfigPayload;
  models: ASRModelOption[];
};

export type MediaModelKind = "image" | "video" | "tts" | "lipsync" | "digital-human" | "voice-clone";

export type TTSVoiceOption = {
  voice_id: string;
  label: string;
  language: string;
  gender?: string;
  style?: string;
  mode: "preset" | "custom_voice_id" | "prompt_controlled" | "instruct_prompt";
  sample_text: string;
};

export type MediaModelOption = {
  model: string;
  label: string;
  description?: string;
  price_summary?: string;
  voices?: TTSVoiceOption[];
  voice_modes?: string[];
  capabilities?: string[];
  supports_prompt_builder?: boolean;
};

export type MediaProviderConfigPayload = {
  kind: MediaModelKind;
  provider: string;
  provider_label: string;
  description: string;
  docs_url: string;
  models: MediaModelOption[];
  model: string;
  enabled: boolean;
  active: boolean;
  has_api_key: boolean;
  api_key_ref: string;
  updated_at: number | null;
  voice_guide_url?: string;
  selected_voice_by_model?: Record<string, string>;
  extra_json?: Record<string, any>;
};

export type MediaAgentModelAlias = {
  alias: string;
  provider: string;
  model: string;
  created_at?: number | null;
  updated_at?: number | null;
};

export type MediaModelConfigResponse = {
  kind: MediaModelKind;
  active_provider: string;
  providers: MediaProviderConfigPayload[];
  agent_model_aliases?: MediaAgentModelAlias[];
};

export type ConnectionTestResponse = {
  ok: boolean;
  status: "success" | "failed";
  message: string;
  detail: string;
};

export type TTSVoicePreviewResponse = {
  ok: boolean;
  preview_id: string;
  provider: string;
  model: string;
  voice_id: string;
  audio_url: string;
  duration_seconds: number;
};

export type SkillPayload = {
  kind: string;
  title: string;
  content: string;
  updated_at: number;
  default_content: string;
};

export type TaskPayload = {
  id: number;
  kind: string;
  status: string;
  session_id: string | null;
  summary: string | null;
  error: string | null;
  skill_snapshot: string | null;
  created_at: number;
  started_at: number | null;
  finished_at: number | null;
};

export type TaskLogPayload = {
  id: number;
  task_id: number;
  phase: string;
  level: string;
  message: string;
  created_at: number;
};

export type PublishConfigPayload = {
  status: string;
  input_url: string;
  normalized_url: string;
  scheme: string;
  domain: string;
  path_prefix: string;
  deployment_mode: string;
  local_frontend_url: string;
  local_backend_api_url: string;
  public_api_url: string;
  allowed_hosts_hint: string;
  guide_markdown: string;
  nginx_config: string;
  nps_config: string;
  message: string;
  last_error: string | null;
  test_detail: string | null;
  updated_at: number | null;
  tested_at: number | null;
};

export type OpenFlowAnalysisInput = {
  session_id: number;
  session_title?: string;
  session_status?: string;
  reference_video_path: string;
  industry: string;
  persona: string;
  product_info: string;
  target_audience: string;
  constraints: string;
  analysis_goal: string;
  video_formula: string;
  simple_prompt: string;
  final_prompt: string;
  prompt_model_provider: string;
  prompt_model_id: string;
  version_name: string;
  version_notes: string;
  versions: Array<{
    id: string;
    name: string;
    notes: string;
    simple_prompt: string;
    final_prompt: string;
    prompt_model_provider: string;
    prompt_model_id: string;
    input: Record<string, string>;
    updated_at: number;
  }>;
  generated_skill_content?: string;
  skill_model_provider?: string;
  skill_model_id?: string;
  skill_version_name?: string;
  skill_version_notes?: string;
};

export type OpenFlowSkillBuilderDraft = {
  session_id: number;
  generated_skill_content: string;
  skill_model_provider: string;
  skill_model_id: string;
  skill_version_name: string;
  skill_version_notes: string;
  versions: Array<{
    id: string;
    name: string;
    notes: string;
    content: string;
    skill_model_provider: string;
    skill_model_id: string;
    updated_at: number;
  }>;
  latest_prompt_version: {
    id: string;
    name: string;
    updated_at: number;
  } | null;
};

export type OpenFlowPromptModelOption = {
  providerID: string;
  providerName: string;
  modelID: string;
  modelName: string;
  reasoning: boolean;
  contextLimit: number;
  inputModalities: string[];
};

export type OpenFlowPromptModelsPayload = {
  items: OpenFlowPromptModelOption[];
  default_model: {
    providerID: string;
    modelID: string;
  };
  error?: string;
};

export type OpenFlowPackageSpec = {
  workflow: string;
  summary_format: string;
  export_all_scheme_videos: boolean;
  dialogues_per_scheme: boolean;
  schemes: Array<{ id: string; name: string; label: string; recommended?: boolean }>;
  required_files: string[];
  clip_naming_rule: string;
  clip_examples: string[];
};

export type OpenFlowConfigPayload = {
  default_simple_prompt: string;
  draft: OpenFlowAnalysisInput;
  skill: SkillPayload;
  package_spec: OpenFlowPackageSpec;
  prompt_models: OpenFlowPromptModelsPayload;
  skill_builder: OpenFlowSkillBuilderDraft;
  options: {
    industry: string[];
    persona: string[];
    target_audience: string[];
    analysis_goal: string[];
    video_formula: string[];
  };
};

export type OpenFlowGeneratePayload = {
  ok: boolean;
  draft: OpenFlowAnalysisInput;
  simple_prompt: string;
  final_prompt: string;
  package_spec: OpenFlowPackageSpec;
  skill: SkillPayload;
  prompt_models: OpenFlowPromptModelsPayload;
  skill_builder: OpenFlowSkillBuilderDraft;
  used_prompt_model_provider: string;
  used_prompt_model_id: string;
};

export type OpenFlowSkillBuilderPayload = {
  ok: boolean;
  draft: OpenFlowSkillBuilderDraft;
  skill: SkillPayload;
  prompt_models?: OpenFlowPromptModelsPayload;
  used_skill_model_provider?: string;
  used_skill_model_id?: string;
};

export type OpenFlowRunPayload = {
  ok: boolean;
  session_id: number;
  task_url: string;
  package_spec: OpenFlowPackageSpec;
  used_skill_version_id?: string;
  used_skill_version_name?: string;
};

export type SessionFilePayload = {
  id: number;
  file_id: string;
  path: string;
  kind: string;
  size: number;
  origin: string;
  downloadable: number;
  updated_at: number;
};

export type SessionPayload = {
  id: number;
  source: string;
  group_id: string;
  sender_id?: string | null;
  sender_name?: string | null;
  title: string;
  command_text?: string | null;
  status: string;
  opencode_session_id?: string | null;
  workspace_dir: string;
  share_token?: string | null;
  share_url?: string | null;
  task_url?: string | null;
  last_summary?: string | null;
  files: SessionFilePayload[];
  events_count: number;
  execution_seconds?: number;
  created_at: number;
  updated_at: number;
  started_at?: number | null;
  finished_at?: number | null;
};

export type SessionEventPayload = {
  id: number;
  session_id: number;
  kind: string;
  payload: Record<string, unknown>;
  created_at: number;
};

export type SessionImSendPayload = {
  ok: boolean;
  kind: string;
  reply: string;
  session?: SessionPayload;
  items?: SessionPayload[];
};

export type SessionTaskListPayload = {
  items: SessionPayload[];
  summary: {
    total: number;
    running: number;
    waiting: number;
    failed: number;
    cpu_percent: number;
    memory_percent: number;
    memory_used_mb: number;
  };
};

export type SessionTaskMessagePayload = {
  id: number;
  role: string;
  source: string;
  content: string;
  created_at: number;
};

export type SessionTaskDetailPayload = SessionPayload & {
  task_id: number;
  task_number: string;
  task_label: string;
  messages: SessionTaskMessagePayload[];
  logs: string[];
};

export type SessionTaskFilesPayload = {
  path: string;
  files: Array<{
    name: string;
    type: string;
    size: number;
    path: string;
  }>;
};

export type MediaLibraryAssetPayload = {
  asset_id: string;
  session_id?: number | null;
  display_name: string;
  original_filename: string;
  source_video_path?: string | null;
  media_type: "video";
  thumbnail_url?: string | null;
  preview_url?: string | null;
  duration_ms?: number | null;
  width?: number | null;
  height?: number | null;
  format?: string | null;
  size_bytes?: number | null;
  language?: string | null;
  dialogue_summary?: string | null;
  analysis_status: "not_analyzed" | "queued" | "running" | "processing" | "blocked" | "partial" | "ready" | "stale" | "failed";
  upload_status?: "uploading" | "ready" | "failed" | null;
  subtitle_mode?: "embedded" | "none" | "unknown" | "ocr_pending" | null;
  analysis_summary?: {
    dialogue_fragment_count?: number | null;
    visual_fragment_count?: number | null;
    composite_fragment_count?: number | null;
    keep_count?: number | null;
    review_count?: number | null;
    exclude_count?: number | null;
    editing_issue_count?: number | null;
    top_editing_issue?: string | null;
    top_editing_issue_count?: number | null;
  } | null;
  tags: string[];
  archived: boolean;
  referenced_by_count?: number;
  created_at?: string | number | null;
  updated_at?: string | number | null;
  open_cut?: {
    task_id?: number | null;
    session_id?: number | null;
    status?: string;
    dialogue_status?: string;
    visual_status?: string;
    composite_status?: string;
    dialogue_tool_use_session_id?: string | null;
    dialogue_error?: string | null;
    dialogue_progress?: { step?: string; label?: string; completed?: number; total?: number; started_at?: number; updated_at?: number; finished_at?: number; elapsed_ms?: number };
    visual_tool_use_session_id?: string | null;
    visual_error?: string | null;
    visual_progress?: { step?: string; label?: string; completed?: number; total?: number; started_at?: number; updated_at?: number; finished_at?: number; elapsed_ms?: number };
    counts?: { dialogue?: number | null; visual?: number | null; composite?: number | null };
  };
  analysis_results?: {
    dialogue?: { items?: Array<Record<string, unknown>>; error?: string | null };
    visual?: { items?: Array<Record<string, unknown>>; error?: string | null };
    composite?: { items?: Array<Record<string, unknown>>; error?: string | null };
  };
};

export type MediaLibraryListPayload = {
  items: MediaLibraryAssetPayload[];
  total: number;
  page: number;
  page_size: number;
  facets?: { tags?: string[] };
};

export type MediaLibraryListParams = {
  q?: string;
  analysis_status?: string;
  subtitle_mode?: string;
  duration_range?: string;
  tag?: string;
  updated_range?: string;
  orientation?: string;
  include_archived?: boolean;
  sort?: string;
  page?: number;
  page_size?: number;
};

export type MediaLibraryUploadPayload = {
  upload_id: string;
  asset_id: string;
  session_id: number;
  filename: string;
  size_bytes: number;
  chunk_size: number;
  total_chunks: number;
  received_chunks: number[];
  received_bytes: number;
  status: "uploading" | "finalizing" | "ready" | "failed";
  error?: string | null;
};

export type MediaLibrarySearchMatchedFragmentPayload = {
  scheme: string;
  analysis_scheme?: string | null;
  run_id?: string | null;
  fragment_id: string;
  start_ms: number;
  end_ms: number;
  dialogue_text?: string | null;
  summary?: string | null;
  keyframe_url?: string | null;
};

export type MediaLibrarySearchCandidatePayload = {
  source: "media_library" | "external" | "local" | string;
  candidate_kind?: "original_video" | "derived_clip" | string | null;
  candidate_id: string;
  asset_id?: string | null;
  source_asset_id?: string | null;
  source_clip_id?: string | null;
  source_version?: string | null;
  content_sha256?: string | null;
  display_name: string;
  preview_url?: string | null;
  thumbnail_url?: string | null;
  duration_ms?: number | null;
  tags?: string[];
  candidate_start_ms?: number | null;
  candidate_end_ms?: number | null;
  source_start_ms?: number | null;
  source_end_ms?: number | null;
  time_basis?: "candidate" | string | null;
  orientation?: string | null;
  score?: number | null;
  score_reasons?: string[];
  matched_fragments?: MediaLibrarySearchMatchedFragmentPayload[];
  allowed_actions: Array<"preview" | "open_editor" | "import_original" | "import_clip" | "import_whole" | string>;
};

export type MediaLibrarySearchRunPayload = {
  search_id: string;
  retrieval_version?: string;
  planner_degraded: boolean;
  result_count: number;
  items: MediaLibrarySearchCandidatePayload[];
};

export type StoryboardMediaLibrarySearchInput = {
  user_text?: string;
  orientation?: string;
  limit?: number;
};

export type StoryboardMediaLibraryImportInput = {
  source_kind: "media_library_original" | "media_library_clip";
  source_id: string;
  target_task_id: number;
  requested_name?: string;
  search_id: string;
  dialogue_asset_key?: string | null;
  idempotency_key: string;
};

export type StoryboardMediaLibraryImportPayload = {
  ok?: boolean;
  import_id?: string;
  item?: Record<string, unknown>;
  task?: Record<string, unknown>;
  meta?: Record<string, unknown>;
  plan?: Record<string, unknown>;
};

export type MediaLibraryVisualCurrentPayload = {
  run: {
    analysis_run_id: string;
    scheme: "visual_structure" | "visual_semantic";
    status: string;
    schema_version?: string | null;
    prompt_version?: string | null;
    model_config_label?: string | null;
    model_alias?: string | null;
    model_version?: string | null;
    sampling_strategy?: string | null;
    progress?: Record<string, unknown>;
    error?: Record<string, unknown> | string | null;
  } | null;
  items: Array<Record<string, unknown>>;
};

export type MediaLibraryVisualRunInput = {
  force_structure?: boolean;
  force_semantic?: boolean;
  allow_cloud_visual_data_transfer: boolean;
};

export type MediaLibraryVisualRunPayload = {
  status: string;
  structure_run_id?: string | null;
  semantic_run_id?: string | null;
  operation_id?: string | null;
};

export type MediaLibraryCompositeCurrentPayload = {
  run: {
    analysis_run_id: string;
    scheme: "composite";
    status: string;
    schema_version?: string | null;
    prompt_version?: string | null;
    model_config_label?: string | null;
    model_alias?: string | null;
    model_version?: string | null;
    progress?: Record<string, unknown>;
    error?: Record<string, unknown> | string | null;
  } | null;
  items: Array<Record<string, unknown>>;
};

export type MediaLibraryCompositeRunPayload = {
  status: string;
  analysis_run_id?: string | null;
  operation_id?: string | null;
};

export type MediaLibraryEditorNavigationInput = {
  start_ms?: number;
  end_ms?: number;
  target_task_id?: number;
  dialogue_asset_key?: string;
  search_id?: string;
  matched_fragment_id?: string;
  return_to?: "storyboard_dialogue" | "media_library_detail";
};

export type MediaLibraryEditorPayload = {
  item: MediaLibraryAssetPayload;
  source_version: string;
  fragments: {
    dialogue: Array<Record<string, unknown>>;
    visual: Array<Record<string, unknown>>;
    composite: Array<Record<string, unknown>>;
  };
  runs: {
    dialogue?: Record<string, unknown> | null;
    visual_structure?: Record<string, unknown> | null;
    visual_semantic?: Record<string, unknown> | null;
    composite?: Record<string, unknown> | null;
  };
  clips: Array<Record<string, unknown>>;
  import_targets: Array<{
    task_id: number;
    session_id: number;
    title: string;
    workflow_mode: string;
    updated_at: number;
  }>;
  navigation_context: Record<string, unknown>;
  fragment_count?: number;
  serialized_bytes?: number;
};

export type MediaLibraryClipJobInput = {
  source_version: string;
  start_ms: number;
  end_ms: number;
  display_name: string;
  source_scheme: string | null;
  source_fragment_id: string | null;
  source_analysis_run_id: string | null;
  source_search_id: string | null;
  source_dialogue_asset_key: string | null;
  manual_override: boolean;
  idempotency_key: string;
};

export type MediaLibraryClipJobPayload = {
  clip_job_id: string;
  status: "queued" | "running" | "completed" | "failed" | "cancelled";
  progress: number;
  clip_id?: string | null;
  error?: Record<string, unknown> | string | null;
  clip?: Record<string, unknown> | null;
};

export type MediaLibraryClipImportInput = {
  target_task_id: number;
  requested_name: string;
  search_id?: string | null;
  dialogue_asset_key?: string | null;
  idempotency_key: string;
};

export type MediaLibraryEditorSearchInput = {
  target_task_id?: number | null;
  sources: Array<"external" | "media_library">;
  fragment_refs: Array<{ scheme: string; run_id: string; fragment_id: string }>;
  user_text: string;
  orientation: string;
  limit: number;
};

const API_BASE = (() => {
  const href = window.location.href.split("#")[0];
  return href.endsWith("/") ? href : `${href}/`;
})();

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const relativePath = path.startsWith("/") ? path.slice(1) : path;
  const res = await fetch(new URL(relativePath, API_BASE), {
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
    ...init,
  });

  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `Request failed (${res.status})`);
  }

  if (res.status === 204) {
    return {} as T;
  }

  return (await res.json()) as T;
}

async function requestForm<T>(path: string, body: FormData): Promise<T> {
  const relativePath = path.startsWith("/") ? path.slice(1) : path;
  const res = await fetch(new URL(relativePath, API_BASE), { method: "POST", credentials: "include", body });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `Request failed (${res.status})`);
  }
  return (await res.json()) as T;
}

function requestUploadChunk<T>(path: string, body: FormData, options: { onProgress?: (loaded: number, total: number) => void; signal?: AbortSignal } = {}): Promise<T> {
  return new Promise((resolve, reject) => {
    const relativePath = path.startsWith("/") ? path.slice(1) : path;
    const xhr = new XMLHttpRequest();
    const abort = () => xhr.abort();
    xhr.open("POST", new URL(relativePath, API_BASE));
    xhr.withCredentials = true;
    xhr.upload.onprogress = (event) => options.onProgress?.(event.loaded, event.lengthComputable ? event.total : 0);
    xhr.onerror = () => reject(new Error("上传网络连接中断，请重试。"));
    xhr.onabort = () => reject(new DOMException("Upload cancelled", "AbortError"));
    xhr.onload = () => {
      if (xhr.status < 200 || xhr.status >= 300) {
        reject(new Error(xhr.responseText || `上传失败 (${xhr.status})`));
        return;
      }
      try {
        resolve(JSON.parse(xhr.responseText || "{}") as T);
      } catch {
        reject(new Error("上传接口返回了无法识别的数据。"));
      }
    };
    if (options.signal) {
      if (options.signal.aborted) {
        abort();
        return;
      }
      options.signal.addEventListener("abort", abort, { once: true });
      xhr.addEventListener("loadend", () => options.signal?.removeEventListener("abort", abort), { once: true });
    }
    xhr.send(body);
  });
}

export const api = {
  authStatus: () => request<{ enabled: boolean; configured: boolean; authenticated: boolean; role: string; capabilities: Record<string, boolean>; debug_console_enabled: boolean }>("/api/auth/status"),
  authSetup: (password: string) => request<{ ok: boolean; role?: string; capabilities?: Record<string, boolean> }>("/api/auth/setup", { method: "POST", body: JSON.stringify({ password }) }),
  authLogin: (password: string) => request<{ ok: boolean; role: string; capabilities: Record<string, boolean> }>("/api/auth/login", { method: "POST", body: JSON.stringify({ password }) }),
  authLogout: () => request<{ ok: boolean }>("/api/auth/logout", { method: "POST" }),
  mediaLibraryCapabilities: () => request<{
    schema_version: "media_library_capabilities_v1";
    features: Record<string, {
      enabled: boolean;
      configuration_valid: boolean;
    }>;
  }>("/api/media-library/capabilities"),
  mediaLibraryList: (params: MediaLibraryListParams = {}) => {
    const query = new URLSearchParams();
    Object.entries(params).forEach(([key, value]) => {
      if (value === "" || value === null || value === undefined || value === false) return;
      query.set(key, value === true ? "true" : String(value));
    });
    const suffix = query.toString();
    return request<MediaLibraryListPayload>(`/api/media-library${suffix ? `?${suffix}` : ""}`);
  },
  mediaLibraryDetail: (assetId: string) => request<{ item: MediaLibraryAssetPayload }>(`/api/media-library/${encodeURIComponent(assetId)}`),
  mediaLibraryUpdate: (assetId: string, input: { display_name?: string; tags?: string[] }) => request<{ item: MediaLibraryAssetPayload }>(`/api/media-library/${encodeURIComponent(assetId)}`, { method: "PATCH", body: JSON.stringify(input) }),
  mediaLibraryArchive: (assetId: string) => request<{ item: MediaLibraryAssetPayload }>(`/api/media-library/${encodeURIComponent(assetId)}/archive`, { method: "POST" }),
  mediaLibraryRestore: (assetId: string) => request<{ item: MediaLibraryAssetPayload }>(`/api/media-library/${encodeURIComponent(assetId)}/restore`, { method: "POST" }),
  mediaLibraryDelete: (assetId: string) => request<{ ok: boolean }>(`/api/media-library/${encodeURIComponent(assetId)}`, { method: "DELETE" }),
  mediaLibraryRunDialogue: (
    assetId: string,
    input: { force?: boolean; allow_cloud_asr_data_transfer?: boolean } = {},
  ) => request<{ status: string; task_id: number; session_id: number }>(
    `/api/media-library/${encodeURIComponent(assetId)}/analyses/dialogue/run`,
    { method: "POST", body: JSON.stringify(input) },
  ),
  mediaLibraryRunVisual: (
    assetId: string,
    input: MediaLibraryVisualRunInput,
  ) => request<MediaLibraryVisualRunPayload>(
    `/api/media-library/${encodeURIComponent(assetId)}/analyses/visual/run`,
    { method: "POST", body: JSON.stringify(input) },
  ),
  mediaLibraryVisualCurrent: (assetId: string) =>
    request<MediaLibraryVisualCurrentPayload>(`/api/media-library/${encodeURIComponent(assetId)}/analyses/visual/current`),
  mediaLibraryCompositeCurrent: (assetId: string) =>
    request<MediaLibraryCompositeCurrentPayload>(`/api/media-library/${encodeURIComponent(assetId)}/analyses/composite/current`),
  mediaLibraryRunComposite: (assetId: string, force = false) =>
    request<MediaLibraryCompositeRunPayload>(
      `/api/media-library/${encodeURIComponent(assetId)}/analyses/composite/run`,
      { method: "POST", body: JSON.stringify({ force }) },
    ),
  mediaLibraryEditor: (assetId: string, navigation: MediaLibraryEditorNavigationInput = {}) => {
    const query = new URLSearchParams();
    Object.entries(navigation).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== "") query.set(key, String(value));
    });
    const suffix = query.toString();
    return request<MediaLibraryEditorPayload>(
      `/api/media-library/${encodeURIComponent(assetId)}/editor${suffix ? `?${suffix}` : ""}`,
    );
  },
  mediaLibraryCreateClipJob: (assetId: string, input: MediaLibraryClipJobInput) =>
    request<MediaLibraryClipJobPayload>(
      `/api/media-library/${encodeURIComponent(assetId)}/clip-jobs`,
      { method: "POST", body: JSON.stringify(input) },
    ),
  mediaLibraryClipJob: (assetId: string, clipJobId: string) =>
    request<MediaLibraryClipJobPayload>(
      `/api/media-library/${encodeURIComponent(assetId)}/clip-jobs/${encodeURIComponent(clipJobId)}`,
    ),
  mediaLibraryCancelClipJob: (assetId: string, clipJobId: string) =>
    request<MediaLibraryClipJobPayload>(
      `/api/media-library/${encodeURIComponent(assetId)}/clip-jobs/${encodeURIComponent(clipJobId)}/cancel`,
      { method: "POST" },
    ),
  mediaLibraryClips: (assetId: string) =>
    request<{ items?: Array<Record<string, unknown>>; clips?: Array<Record<string, unknown>> }>(
      `/api/media-library/${encodeURIComponent(assetId)}/clips`,
    ),
  mediaLibraryDeleteClip: (assetId: string, clipId: string) =>
    request<{ ok: boolean }>(
      `/api/media-library/${encodeURIComponent(assetId)}/clips/${encodeURIComponent(clipId)}`,
      { method: "DELETE" },
    ),
  mediaLibraryUpdateClip: (
    assetId: string,
    clipId: string,
    input: { display_name?: string; tags?: string[]; search_eligible?: boolean },
  ) => request<{ clip: Record<string, unknown> }>(
    `/api/media-library/${encodeURIComponent(assetId)}/clips/${encodeURIComponent(clipId)}`,
    { method: "PATCH", body: JSON.stringify(input) },
  ),
  mediaLibraryImportClip: (assetId: string, clipId: string, input: MediaLibraryClipImportInput) =>
    request<StoryboardMediaLibraryImportPayload>(
      `/api/media-library/${encodeURIComponent(assetId)}/clips/${encodeURIComponent(clipId)}/import-to-storyboard`,
      { method: "POST", body: JSON.stringify(input) },
    ),
  mediaLibraryImportTargets: () => request<{
    items: Array<{ task_id: number; session_id: number; title: string; workflow_mode: string; updated_at: number }>;
  }>("/api/media-library/import-targets/storyboards"),
  mediaLibraryEditorSearchPlan: (assetId: string, input: MediaLibraryEditorSearchInput) =>
    request<Record<string, unknown>>(
      `/api/media-library/${encodeURIComponent(assetId)}/search/plan`,
      { method: "POST", body: JSON.stringify(input) },
    ),
  mediaLibraryEditorSearchRun: (assetId: string, input: MediaLibraryEditorSearchInput) =>
    request<MediaLibrarySearchRunPayload>(
      `/api/media-library/${encodeURIComponent(assetId)}/search/runs`,
      { method: "POST", body: JSON.stringify(input) },
    ),
  mediaLibraryEditorSearchRunResult: (assetId: string, searchId: string) =>
    request<MediaLibrarySearchRunPayload>(
      `/api/media-library/${encodeURIComponent(assetId)}/search/runs/${encodeURIComponent(searchId)}`,
    ),
  mediaLibraryEditorSearchAction: (
    assetId: string,
    searchId: string,
    input: {
      action_kind: "preview" | "open_editor";
      source: "external" | "media_library";
      candidate_id: string;
      metadata?: Record<string, unknown>;
    },
  ) => request<{ ok: boolean; recorded: boolean }>(
    `/api/media-library/${encodeURIComponent(assetId)}/search/runs/${encodeURIComponent(searchId)}/actions`,
    { method: "POST", body: JSON.stringify(input), keepalive: true },
  ),
  mediaLibraryEditorSearchImport: (assetId: string, searchId: string, input: Record<string, unknown>) =>
    request<StoryboardMediaLibraryImportPayload>(
      `/api/media-library/${encodeURIComponent(assetId)}/search/runs/${encodeURIComponent(searchId)}/import-to-storyboard`,
      { method: "POST", body: JSON.stringify(input) },
    ),
  mediaLibraryExternalSearchImport: (
    taskId: number,
    input: { search_id: string; candidate_ids: string[]; label_prefix?: string; confirm_license: boolean },
  ) => request<Record<string, unknown>>(
    `/api/koubo-storyboard/tasks/${encodeURIComponent(taskId)}/asset-library-search/import`,
    { method: "POST", body: JSON.stringify(input) },
  ),
  mediaLibraryUploadCreate: (input: { filename: string; size_bytes: number; content_type?: string }) => request<MediaLibraryUploadPayload>("/api/media-library/uploads", { method: "POST", body: JSON.stringify(input) }),
  mediaLibraryUploadStatus: (uploadId: string) => request<MediaLibraryUploadPayload>(`/api/media-library/uploads/${encodeURIComponent(uploadId)}`),
  mediaLibraryUploadChunk: (uploadId: string, form: FormData, options: { onProgress?: (loaded: number, total: number) => void; signal?: AbortSignal } = {}) => requestUploadChunk<MediaLibraryUploadPayload>(`/api/media-library/uploads/${encodeURIComponent(uploadId)}/chunks`, form, options),
  mediaLibraryUploadComplete: (uploadId: string, sizeBytes: number) => request<{ upload: MediaLibraryUploadPayload; item: MediaLibraryAssetPayload }>(`/api/media-library/uploads/${encodeURIComponent(uploadId)}/complete`, { method: "POST", body: JSON.stringify({ size_bytes: sizeBytes }) }),
  mediaLibraryUploadCancel: (uploadId: string) => request<{ ok: boolean; upload_id: string; session_id: number; asset_id: string }>(`/api/media-library/uploads/${encodeURIComponent(uploadId)}`, { method: "DELETE" }),
  storyboardMediaLibrarySearchPlan: (taskId: number, dialogueAssetKey: string, input: StoryboardMediaLibrarySearchInput = {}) =>
    request<Record<string, unknown>>(`/api/koubo-storyboard/tasks/${encodeURIComponent(taskId)}/dialogues/${encodeURIComponent(dialogueAssetKey)}/media-library-search/plan`, { method: "POST", body: JSON.stringify(input) }),
  storyboardMediaLibrarySearchRun: (taskId: number, dialogueAssetKey: string, input: StoryboardMediaLibrarySearchInput = {}) =>
    request<MediaLibrarySearchRunPayload>(`/api/koubo-storyboard/tasks/${encodeURIComponent(taskId)}/dialogues/${encodeURIComponent(dialogueAssetKey)}/media-library-search/runs`, { method: "POST", body: JSON.stringify(input) }),
  storyboardMediaLibrarySearchRunResult: (taskId: number, searchId: string) =>
    request<MediaLibrarySearchRunPayload>(`/api/koubo-storyboard/tasks/${encodeURIComponent(taskId)}/media-library-search/runs/${encodeURIComponent(searchId)}`),
  storyboardMediaLibraryImport: (taskId: number, input: StoryboardMediaLibraryImportInput) =>
    request<StoryboardMediaLibraryImportPayload>(`/api/koubo-storyboard/tasks/${encodeURIComponent(taskId)}/media-library-search/import`, { method: "POST", body: JSON.stringify(input) }),
  summary: () => request<SummaryPayload>("/api/setup/summary"),
  opencodeDiscover: () => request<OpenCodeDiscoverPayload>("/api/setup/opencode/discover", { method: "POST" }),
  opencodeCheck: (base_url: string, username = "", password = "") =>
    request("/api/setup/opencode/check", { method: "POST", body: JSON.stringify({ base_url, username, password }) }),
  opencodeSave: (base_url: string, username = "", password = "") =>
    request("/api/setup/opencode/save", { method: "POST", body: JSON.stringify({ base_url, username, password }) }),
  asrConfig: () => request<ASRConfigResponse>("/api/setup/asr/config"),
  asrConfigSave: (input: { config_name: string; provider: string; model: string; language: string; api_url: string; api_key: string; enabled: boolean }) =>
    request<ASRConfigResponse & { ok: boolean }>("/api/setup/asr/config", { method: "PUT", body: JSON.stringify(input) }),
  asrConnectionTest: (input: { provider: string; model: string }) =>
    request<ConnectionTestResponse>("/api/setup/asr/test", { method: "POST", body: JSON.stringify(input) }),
  mediaModelConfig: (kind: MediaModelKind) => request<MediaModelConfigResponse>(`/api/setup/media-models/${kind}/config`),
  mediaModelConfigSave: (kind: MediaModelKind, input: { active_provider: string; providers: Array<{ provider: string; model: string; api_key: string; enabled: boolean; selected_voice_by_model?: Record<string, string>; extra?: Record<string, any> }>; agent_model_aliases?: MediaAgentModelAlias[] }) =>
    request<MediaModelConfigResponse & { ok: boolean }>(`/api/setup/media-models/${kind}/config`, { method: "PUT", body: JSON.stringify(input) }),
  mediaModelConnectionTest: (kind: MediaModelKind, input: { provider: string; model: string }) =>
    request<ConnectionTestResponse>(`/api/setup/media-models/${kind}/test`, { method: "POST", body: JSON.stringify(input) }),
  localMeteringReport: (days = 30, limit = 200) =>
    request<any>(`/api/local-metering/report?days=${encodeURIComponent(days)}&limit=${encodeURIComponent(limit)}`),
  localMeteringTaskReport: (taskId: number | string, attempt = "all", limit = 500, includeItems = true) =>
    request<any>(`/api/local-metering/tasks/${encodeURIComponent(taskId)}?attempt=${encodeURIComponent(attempt)}&limit=${encodeURIComponent(limit)}&include_items=${includeItems ? "true" : "false"}`),
  mihomoConfig: () => request<{ enabled: boolean; has_subscription_url: boolean; proxy_url: string; listen_host: string; http_port: number; socks_port: number; status: string; running: boolean }>("/api/setup/mihomo/config"),
  mihomoConfigSave: (input: { enabled: boolean; subscription_url: string }) =>
    request<{ ok: boolean; enabled: boolean }>("/api/setup/mihomo/config", { method: "PUT", body: JSON.stringify(input) }),
  mihomoTest: () => request<ConnectionTestResponse & { running: boolean; proxy_url: string }>("/api/setup/mihomo/test", { method: "POST" }),
  ttsVoicePreview: (input: { provider: string; model: string; voice_id: string; sample_text: string; simple_prompt?: string; complex_prompt?: string }) =>
    request<TTSVoicePreviewResponse>("/api/setup/media-models/tts/voices/preview", { method: "POST", body: JSON.stringify(input) }),
  npcConfig: () => request<{ server_addr: string; public_base_url: string; vkey: string; conn_type: string; auto_reconnection: boolean; max_conn: number; flow_limit: number; rate_limit: number; basic_username: string; basic_password: string; crypt: boolean; compress: boolean; disconnect_timeout: number; mode: string; target_addr: string; server_port: number; multi_account_line: string; conf_text: string; multi_account_text: string; conf_path?: string; multi_account_path?: string }>("/api/setup/npc/config"),
  npcConfigSave: (input: { server_addr: string; public_base_url: string; vkey: string; conn_type: string; auto_reconnection: boolean; max_conn: number; flow_limit: number; rate_limit: number; basic_username: string; basic_password: string; crypt: boolean; compress: boolean; disconnect_timeout: number; mode: string; target_addr: string; server_port: number; multi_account_line: string; conf_text: string; multi_account_text: string }) => request<{ ok: boolean }>("/api/setup/npc/config", { method: "PUT", body: JSON.stringify(input) }),
  npcDetect: () => request<{ ok: boolean }>("/api/setup/npc/detect", { method: "POST" }),
  npcInstall: () => request<{ ok: boolean; task_id: number }>("/api/setup/npc/install", { method: "POST" }),
  npcRun: (input: { server_addr: string; public_base_url: string; vkey: string; conn_type: string; auto_reconnection: boolean; max_conn: number; flow_limit: number; rate_limit: number; basic_username: string; basic_password: string; crypt: boolean; compress: boolean; disconnect_timeout: number; mode: string; target_addr: string; server_port: number; multi_account_line: string; conf_text: string; multi_account_text: string }) => request<{ ok: boolean; task_id: number }>("/api/setup/npc/run", { method: "POST", body: JSON.stringify(input) }),
  npcReconnect: (input: { server_addr: string; public_base_url: string; vkey: string; conn_type: string; auto_reconnection: boolean; max_conn: number; flow_limit: number; rate_limit: number; basic_username: string; basic_password: string; crypt: boolean; compress: boolean; disconnect_timeout: number; mode: string; target_addr: string; server_port: number; multi_account_line: string; conf_text: string; multi_account_text: string }) => request<{ ok: boolean; task_id: number }>("/api/setup/npc/reconnect", { method: "POST", body: JSON.stringify(input) }),
  npcStop: () => request<{ ok: boolean; server_addr: string; stopped_count: number; failed_count: number; message: string }>("/api/setup/npc/stop", { method: "POST" }),
  npcUninstall: () => request<{ ok: boolean; task_id: number }>("/api/setup/npc/uninstall", { method: "POST" }),
  npcTask: (taskId: number) => request<TaskPayload>(`/api/setup/npc/tasks/${taskId}`),
  npcTaskLogs: (taskId: number) => request<{ items: TaskLogPayload[] }>(`/api/setup/npc/tasks/${taskId}/logs`),
  npcSkill: (kind: "install" | "run" | "uninstall") => request<SkillPayload>(`/api/setup/npc/skills/${kind}`),
  npcSkillSave: (kind: "install" | "run" | "uninstall", content: string) =>
    request<{ ok: boolean }>(`/api/setup/npc/skills/${kind}`, { method: "PUT", body: JSON.stringify({ content }) }),
  npcSkillRestore: (kind: "install" | "run" | "uninstall") =>
    request<{ ok: boolean }>(`/api/setup/npc/skills/${kind}/restore-default`, { method: "POST" }),
  publishConfig: () => request<PublishConfigPayload>("/api/setup/publish/config"),
  publishConfigSave: (url: string) => request<PublishConfigPayload & { ok: boolean }>("/api/setup/publish/config", { method: "PUT", body: JSON.stringify({ url }) }),
  publishRecommend: (url: string) => request<PublishConfigPayload & { ok: boolean }>("/api/setup/publish/recommend", { method: "POST", body: JSON.stringify({ url }) }),
  publishTest: (url: string) => request<PublishConfigPayload & { ok: boolean; checks: Array<{ name: string; ok: boolean; message: string; category: string; severity: string; recommended_fix: string }> }>("/api/setup/publish/test", { method: "POST", body: JSON.stringify({ url }) }),
  publishValidate: (url: string) => request<{ ok: boolean; task_id: number }>("/api/setup/publish/validate", { method: "POST", body: JSON.stringify({ url }) }),
  publishTask: (taskId: number) => request<TaskPayload>(`/api/setup/publish/tasks/${taskId}`),
  publishTaskLogs: (taskId: number) => request<{ items: TaskLogPayload[] }>(`/api/setup/publish/tasks/${taskId}/logs`),
  publishSkill: () => request<SkillPayload>("/api/setup/publish/skills/validate"),
  publishSkillSave: (content: string) => request<{ ok: boolean }>("/api/setup/publish/skills/validate", { method: "PUT", body: JSON.stringify({ content }) }),
  publishSkillRestore: () => request<{ ok: boolean }>("/api/setup/publish/skills/validate/restore-default", { method: "POST" }),
  sessions: (group_id?: string) => request<{ items: SessionPayload[] }>(`/api/sessions${group_id ? `?group_id=${encodeURIComponent(group_id)}` : ""}`),
  sessionTasks: (group_id?: string) => request<SessionTaskListPayload>(`/api/session-tasks${group_id ? `?group_id=${encodeURIComponent(group_id)}` : ""}`),
  sessionTaskDetail: (sessionId: number) => request<SessionTaskDetailPayload>(`/api/session-tasks/${sessionId}`),
  sessionTaskDelete: (sessionId: number) => request<{ ok: boolean; deleted_id: number; deleted_title: string }>(`/api/session-tasks/${sessionId}`, { method: "DELETE" }),
  sessionTaskCancel: (sessionId: number) => request<{ ok: boolean; task_id: number; status: string }>(`/api/session-tasks/${sessionId}/cancel`, { method: "POST" }),
  sessionTaskRerun: (sessionId: number) => request<{ ok: boolean; session_id: number; session: SessionTaskDetailPayload; task_url: string }>(`/api/session-tasks/${sessionId}/rerun`, { method: "POST" }),
  sessionTaskFiles: (sessionId: number, path = "") => request<SessionTaskFilesPayload>(`/api/session-tasks/${sessionId}/files${path ? `?path=${encodeURIComponent(path)}` : ""}`),
  sessionTaskLogs: (sessionId: number) => request<{ lines: string[] }>(`/api/session-tasks/${sessionId}/logs`),
  session: (sessionId: number) => request<SessionPayload>(`/api/sessions/${sessionId}`),
  sessionEvents: (sessionId: number, since = 0, audience = "customer") => request<{ items: SessionEventPayload[] }>(`/api/sessions/${sessionId}/events?since=${since}&audience=${encodeURIComponent(audience)}`),
  sessionFiles: (sessionId: number) => request<{ items: SessionFilePayload[] }>(`/api/sessions/${sessionId}/files`),
  sessionSendIm: (input: { sender_name: string; group_id: string; message: string; active_session_id?: number | null }) =>
    request<SessionImSendPayload>("/api/sessions/im/send", { method: "POST", body: JSON.stringify(input) }),
  sessionShare: async (sessionId: number, scope = "collaborator") => {
    const form = new FormData();
    form.append("scope", scope);
    return requestForm<{ ok: boolean; token: string; url: string; scope: string; expires_at: number }>(`/api/sessions/${sessionId}/share`, form);
  },
  sessionUploadFile: async (sessionId: number, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return requestForm<{ ok: boolean; path: string; bytes: number }>(`/api/sessions/${sessionId}/files`, form);
  },
  sessionTaskUploadFile: async (sessionId: number, file: File, path = "") => {
    const form = new FormData();
    form.append("file", file);
    return requestForm<{ ok: boolean; path: string; bytes: number }>(`/api/session-tasks/${sessionId}/upload${path ? `?path=${encodeURIComponent(path)}` : ""}`, form);
  },
  sessionDownloadUrl: (sessionId: number, fileId: string) => new URL(`api/sessions/${sessionId}/files/${encodeURIComponent(fileId)}`, API_BASE).toString(),
  sessionTaskRawFileUrl: (sessionId: number, filePath: string) => new URL(`api/session-tasks/${sessionId}/raw/${filePath.split("/").map(encodeURIComponent).join("/")}`, API_BASE).toString(),
  sessionTaskZipUrl: (sessionId: number, path = "") => new URL(`api/session-tasks/${sessionId}/files.zip${path ? `?path=${encodeURIComponent(path)}` : ""}`, API_BASE).toString(),
  sessionEventStreamUrl: (since = 0) => new URL(`api/events/sessions/stream?since=${since}`, API_BASE).toString(),
  sessionScopedEventStreamUrl: (sessionId: number, since = 0, audience = "customer") => new URL(`api/sessions/${sessionId}/events/stream?since=${since}&audience=${encodeURIComponent(audience)}`, API_BASE).toString(),
  taskDetailUrl: (sessionId: number) => `${window.location.origin}${window.location.pathname}#/sessions/task/${sessionId}`,
};
