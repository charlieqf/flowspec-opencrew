import type {
  ASRConfigResponse,
  ConnectionTestResponse,
  MediaModelConfigResponse,
  TTSVoicePreviewResponse,
} from "./types";

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
  return (await res.json()) as T;
}

export const modelConfigApi = {
  asrConfig: () => request<ASRConfigResponse>("/api/model-config/asr/config"),
  asrConfigSave: (input: { config_name: string; provider: string; model: string; language: string; api_url: string; api_key: string; enabled: boolean }) =>
    request<ASRConfigResponse & { ok: boolean }>("/api/model-config/asr/config", { method: "PUT", body: JSON.stringify(input) }),
  asrConnectionTest: (input: { provider: string; model: string }) =>
    request<ConnectionTestResponse>("/api/model-config/asr/test", { method: "POST", body: JSON.stringify(input) }),

  imageConfig: () => request<MediaModelConfigResponse>("/api/model-config/image/config"),
  imageConfigSave: (input: { active_provider: string; providers: Array<{ provider: string; model: string; api_key: string; enabled: boolean }>; agent_model_aliases?: Array<{ alias: string; provider: string; model: string; created_at?: number | null; updated_at?: number | null }> }) =>
    request<MediaModelConfigResponse & { ok: boolean }>("/api/model-config/image/config", { method: "PUT", body: JSON.stringify(input) }),
  imageConnectionTest: (input: { provider: string; model: string }) =>
    request<ConnectionTestResponse>("/api/model-config/image/test", { method: "POST", body: JSON.stringify(input) }),

  videoConfig: () => request<MediaModelConfigResponse>("/api/model-config/video/config"),
  videoConfigSave: (input: { active_provider: string; providers: Array<{ provider: string; model: string; api_key: string; enabled: boolean }>; agent_model_aliases?: Array<{ alias: string; provider: string; model: string; created_at?: number | null; updated_at?: number | null }> }) =>
    request<MediaModelConfigResponse & { ok: boolean }>("/api/model-config/video/config", { method: "PUT", body: JSON.stringify(input) }),
  videoConnectionTest: (input: { provider: string; model: string }) =>
    request<ConnectionTestResponse>("/api/model-config/video/test", { method: "POST", body: JSON.stringify(input) }),

  lipsyncConfig: () => request<MediaModelConfigResponse>("/api/model-config/lipsync/config"),
  lipsyncConfigSave: (input: { active_provider: string; providers: Array<{ provider: string; model: string; api_key: string; enabled: boolean }> }) =>
    request<MediaModelConfigResponse & { ok: boolean }>("/api/model-config/lipsync/config", { method: "PUT", body: JSON.stringify(input) }),
  lipsyncConnectionTest: (input: { provider: string; model: string }) =>
    request<ConnectionTestResponse>("/api/model-config/lipsync/test", { method: "POST", body: JSON.stringify(input) }),

  digitalHumanConfig: () => request<MediaModelConfigResponse>("/api/model-config/digital-human/config"),
  digitalHumanConfigSave: (input: { active_provider: string; providers: Array<{ provider: string; model: string; api_key: string; enabled: boolean }> }) =>
    request<MediaModelConfigResponse & { ok: boolean }>("/api/model-config/digital-human/config", { method: "PUT", body: JSON.stringify(input) }),
  digitalHumanConnectionTest: (input: { provider: string; model: string }) =>
    request<ConnectionTestResponse>("/api/model-config/digital-human/test", { method: "POST", body: JSON.stringify(input) }),

  voiceCloneConfig: () => request<MediaModelConfigResponse>("/api/model-config/voice-clone/config"),
  voiceCloneConfigSave: (input: { active_provider: string; providers: Array<{ provider: string; model: string; api_key: string; enabled: boolean; selected_voice_by_model?: Record<string, string>; extra?: Record<string, any> }> }) =>
    request<MediaModelConfigResponse & { ok: boolean }>("/api/model-config/voice-clone/config", { method: "PUT", body: JSON.stringify(input) }),
  voiceCloneConnectionTest: (input: { provider: string; model: string }) =>
    request<ConnectionTestResponse>("/api/model-config/voice-clone/test", { method: "POST", body: JSON.stringify(input) }),

  ttsConfig: () => request<MediaModelConfigResponse>("/api/model-config/tts/config"),
  ttsConfigSave: (input: { active_provider: string; providers: Array<{ provider: string; model: string; api_key: string; enabled: boolean; selected_voice_by_model?: Record<string, string>; extra?: Record<string, any> }> }) =>
    request<MediaModelConfigResponse & { ok: boolean }>("/api/model-config/tts/config", { method: "PUT", body: JSON.stringify(input) }),
  ttsConnectionTest: (input: { provider: string; model: string }) =>
    request<ConnectionTestResponse>("/api/model-config/tts/test", { method: "POST", body: JSON.stringify(input) }),
  ttsVoicePreview: (input: { provider: string; model: string; config_kind?: string; voice_id: string; second_voice_id?: string; speaker_1?: string; speaker_2?: string; sample_text: string; simple_prompt?: string; complex_prompt?: string; multi_speaker?: boolean; language?: string }) =>
    request<TTSVoicePreviewResponse>("/api/model-config/tts/voices/preview", { method: "POST", body: JSON.stringify(input) }),
};
