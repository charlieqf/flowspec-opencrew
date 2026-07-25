export type ConnectionTestState = {
  status: "idle" | "testing" | "success" | "failed";
  message: string;
  detail: string;
  expanded: boolean;
};

export type ConnectionTestResponse = {
  ok: boolean;
  status: "success" | "failed";
  message: string;
  detail: string;
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
  kind: "image" | "video" | "tts" | "lipsync" | "digital-human" | "voice-clone";
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
  credential_fields?: Array<{
    key: string;
    label: string;
    type?: "password" | "text";
    required_group?: string;
  }>;
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
  kind: "image" | "video" | "tts" | "lipsync" | "digital-human" | "voice-clone";
  active_provider: string;
  providers: MediaProviderConfigPayload[];
  agent_model_aliases?: MediaAgentModelAlias[];
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

export type TTSPreviewState = {
  status: "idle" | "generating" | "ready" | "failed" | "playing";
  audioUrl: string;
  error: string;
};

export type UsdCnyRateState = {
  rate: number;
  date: string;
  source: string;
  loading: boolean;
  error: string;
};
