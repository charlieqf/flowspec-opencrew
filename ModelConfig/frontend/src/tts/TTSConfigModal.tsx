import { For, Show, createEffect, createMemo, createSignal } from "solid-js";
import { modelConfigApi } from "../shared/api";
import { ConnectionTestControl, defaultConnectionTestState, testStateKey } from "../shared/ConnectionTestControl";
import { CloseIcon, PlayIcon } from "../shared/icons";
import type { ConnectionTestState, MediaModelConfigResponse, TTSPreviewState } from "../shared/types";
import { QwenVoiceGuide } from "./QwenVoiceGuide";
import { XAI_LANGUAGE_OPTIONS, XaiVoiceGuide } from "./XaiVoiceGuide";
import { GOOGLE_TTS_SCENARIOS } from "../../../../frontend/src/shared/tts/googleTtsScenarioGuide";

type Provider = MediaModelConfigResponse["providers"][number];
type ByteDanceCredentialDraft = {
  appId: string;
  accessToken: string;
};

let previewAudio: HTMLAudioElement | null = null;

const DEFAULT_SAMPLE_TEXT = "欢迎使用 OpenCrew。下面这段声音将用于测试语气、节奏、清晰度和商业短视频旁白的自然程度。";
const ENGLISH_SAMPLE_TEXT = "Welcome to OpenCrew. This voice sample tests tone, pacing, clarity, and how natural it feels for a concise commercial narration.";
const GOOGLE_VOICE_LIBRARY_URL = "https://aistudio.google.com/app/generate-speech";
const LANGUAGE_OPTIONS = [
  { value: "zh", label: "中文" },
  { value: "en", label: "English" },
];

type VoiceConfigKind = "tts" | "voice-clone";

const GOOGLE_EMOTION_AUDIO_TAGS = [
  { tag: "[amazed]", zh: "惊讶" },
  { tag: "[bored]", zh: "无聊 / 厌倦" },
  { tag: "[crying]", zh: "哭泣" },
  { tag: "[curious]", zh: "好奇" },
  { tag: "[excited]", zh: "兴奋" },
  { tag: "[excitedly]", zh: "兴奋地" },
  { tag: "[mischievously]", zh: "调皮地 / 淘气地" },
  { tag: "[panicked]", zh: "惊慌地" },
  { tag: "[reluctantly]", zh: "不情愿地" },
  { tag: "[sarcastic]", zh: "讽刺地" },
  { tag: "[sarcastically]", zh: "讽刺地" },
  { tag: "[serious]", zh: "严肃地" },
  { tag: "[tired]", zh: "疲惫地" },
  { tag: "[trembling]", zh: "颤抖地" },
];

const GOOGLE_DELIVERY_AUDIO_TAGS = [
  { tag: "[whispers]", zh: "低语 / 耳语" },
  { tag: "[shouting]", zh: "喊叫 / 高声强调" },
];

const GOOGLE_PACING_AUDIO_TAGS = [
  { tag: "[very fast]", zh: "非常快" },
  { tag: "[very slow]", zh: "非常慢" },
  { tag: "[sarcastically, one painfully slow word at a time]", zh: "讽刺地、一字一顿地慢说" },
];

const GOOGLE_NON_VERBAL_AUDIO_TAGS = [
  { tag: "[sighs]", zh: "叹气" },
  { tag: "[gasp]", zh: "倒吸一口气 / 吃惊吸气" },
  { tag: "[giggles]", zh: "咯咯笑" },
  { tag: "[laughs]", zh: "大笑 / 笑出声" },
  { tag: "[cough]", zh: "咳嗽" },
  { tag: "[yawn]", zh: "打哈欠" },
];

const GOOGLE_AUDIO_TAGS = [
  ...GOOGLE_EMOTION_AUDIO_TAGS,
  ...GOOGLE_DELIVERY_AUDIO_TAGS,
  ...GOOGLE_PACING_AUDIO_TAGS,
  ...GOOGLE_NON_VERBAL_AUDIO_TAGS,
];

const googleAudioTagsForScenario = (scenarioId: string) => {
  if (scenarioId === "non-verbal") return GOOGLE_NON_VERBAL_AUDIO_TAGS;
  if (scenarioId === "local-tone-shift") return GOOGLE_DELIVERY_AUDIO_TAGS;
  if (scenarioId === "pacing-control") return GOOGLE_PACING_AUDIO_TAGS;
  if (scenarioId === "multi-character-contrast") return [
    ...GOOGLE_EMOTION_AUDIO_TAGS.filter((item) => ["[tired]", "[excited]"].includes(item.tag)),
    ...GOOGLE_NON_VERBAL_AUDIO_TAGS.filter((item) => item.tag === "[yawn]"),
  ];
  if (scenarioId === "advanced-director") return GOOGLE_AUDIO_TAGS;
  return [
    ...GOOGLE_EMOTION_AUDIO_TAGS,
    ...GOOGLE_DELIVERY_AUDIO_TAGS,
    ...GOOGLE_NON_VERBAL_AUDIO_TAGS,
  ];
};

const GOOGLE_AUDIO_TAG_SCENARIO_IDS = new Set(["emotion-tags", "local-tone-shift", "pacing-control", "non-verbal", "multi-character-contrast", "advanced-director"]);

const GOOGLE_DOCUMENTED_ACCENTS = [
  { country: "United States", region: "Southern California / Laguna Beach", prompt: "Southern California valley girl from Laguna Beach", zh: "美国南加州 Laguna Beach 的 Valley Girl 风格" },
  { country: "United Kingdom", region: "Brixton, London", prompt: "Jaz is a DJ from Brixton, London", zh: "英国伦敦 Brixton 的 DJ / 电台主持人口音气质" },
  { country: "United Kingdom", region: "Croydon", prompt: "British English accent from Croydon", zh: "英国 Croydon 的英式英语口音" },
];

function defaultSampleText(language: string) {
  return language === "en" ? ENGLISH_SAMPLE_TEXT : DEFAULT_SAMPLE_TEXT;
}

function selectedModel(provider: Provider) {
  return provider.models.find((model) => model.model === provider.model) ?? provider.models[0] ?? null;
}

function selectedVoiceId(provider: Provider) {
  const model = selectedModel(provider);
  if (!model) return "";
  const storedVoiceId = provider.selected_voice_by_model?.[model.model] || "";
  const storedVoice = model.voices?.find((voice) => voice.voice_id === storedVoiceId);
  if (storedVoice && !(provider.provider === "xai" && storedVoice.mode === "custom_voice_id")) return storedVoiceId;
  return model.voices?.find((voice) => voice.mode !== "custom_voice_id")?.voice_id || model.voices?.[0]?.voice_id || "";
}

function previewKey(provider: string, model: string, voiceId: string) {
  return `${provider}/${model}/${voiceId}`;
}

function modelKey(provider: string, model: string) {
  return `${provider}/${model}`;
}

function voiceModeFor(provider: Provider) {
  const model = selectedModel(provider);
  const voiceId = selectedVoiceId(provider);
  const voice = model?.voices?.find((item) => item.voice_id === voiceId) ?? model?.voices?.[0];
  if (voice?.mode === "custom_voice_id") return "custom_voice_id";
  if (model?.supports_prompt_builder || model?.voice_modes?.includes("instruct_prompt") || voice?.mode === "instruct_prompt") return "instruct_prompt";
  return "preset";
}

function visibleVoices(provider: Provider) {
  return selectedModel(provider)?.voices?.filter((voice) => voice.mode !== "custom_voice_id") ?? [];
}

function buildComplexPrompt(simple: string, provider: string, model: string) {
  const base = simple.trim() || "自然、清晰、适合中文商业短视频旁白";
  const isQwen = provider === "qwen" || model.includes("instruct");
  return isQwen
    ? `请将声音塑造成：${base}。要求语速中等，吐字清晰，句尾自然收束，避免机械播报和夸张表演。重点词轻微加强，情绪稳定可信，整体像一位有经验的专业顾问在对目标用户解释方案。`
    : `${base}。请保持自然口语化表达，语速稳定，情绪不过度夸张，重点内容略微强调。`;
}

function byteDanceCredentialPayload(draft: ByteDanceCredentialDraft | undefined) {
  const appId = (draft?.appId || "").trim();
  const accessToken = (draft?.accessToken || "").trim();
  if (!appId && !accessToken) return "";
  if (!appId || !accessToken) return null;
  return JSON.stringify({ app_id: appId, access_token: accessToken });
}

export function TTSConfigModal(props: { open: boolean; onClose: () => void; kind?: VoiceConfigKind; title?: string }) {
  const [loading, setLoading] = createSignal(false);
  const [saving, setSaving] = createSignal(false);
  const [error, setError] = createSignal("");
  const [config, setConfig] = createSignal<MediaModelConfigResponse | null>(null);
  const [apiKeys, setApiKeys] = createSignal<Record<string, string>>({});
  const [byteDanceCredentials, setByteDanceCredentials] = createSignal<Record<string, ByteDanceCredentialDraft>>({});
  const [tests, setTests] = createSignal<Record<string, ConnectionTestState>>({});
  const [voicePreviews, setVoicePreviews] = createSignal<Record<string, TTSPreviewState>>({});
  const [sampleTexts, setSampleTexts] = createSignal<Record<string, string>>({});
  const [simplePrompts, setSimplePrompts] = createSignal<Record<string, string>>({});
  const [complexPrompts, setComplexPrompts] = createSignal<Record<string, string>>({});
  const [customVoiceIds, setCustomVoiceIds] = createSignal<Record<string, string>>({});
  // Per-provider editable extra connection fields supplied by the admin API.
  const [providerExtras, setProviderExtras] = createSignal<Record<string, Record<string, string>>>({});
  const [selectedLanguages, setSelectedLanguages] = createSignal<Record<string, string>>({});
  const [guideProviderId, setGuideProviderId] = createSignal("");
  const [guideScenarioId, setGuideScenarioId] = createSignal(GOOGLE_TTS_SCENARIOS[0].id);
  const [guideSimplePrompts, setGuideSimplePrompts] = createSignal<Record<string, string>>({});
  const [guideComplexPrompts, setGuideComplexPrompts] = createSignal<Record<string, string>>({});
  const [guideInfoScenarioId, setGuideInfoScenarioId] = createSignal("");
  const [guideSecondVoices, setGuideSecondVoices] = createSignal<Record<string, string>>({});

  const configKind = () => props.kind ?? "tts";
  const modalTitle = () => props.title ?? "TTS Model Settings";
  const saveButtonLabel = () => configKind() === "voice-clone" ? "Save Voice Clone Config" : "Save TTS Config";
  const loadingLabel = () => configKind() === "voice-clone" ? "Loading voice clone config..." : "Loading TTS model config...";
  const loadConfig = () => configKind() === "voice-clone" ? modelConfigApi.voiceCloneConfig() : modelConfigApi.ttsConfig();
  const saveConfig = (input: { active_provider: string; providers: Array<{ provider: string; model: string; api_key: string; enabled: boolean; selected_voice_by_model?: Record<string, string>; extra?: Record<string, any> }> }) =>
    configKind() === "voice-clone" ? modelConfigApi.voiceCloneConfigSave(input) : modelConfigApi.ttsConfigSave(input);
  const testConnection = (input: { provider: string; model: string }) =>
    configKind() === "voice-clone" ? modelConfigApi.voiceCloneConnectionTest(input) : modelConfigApi.ttsConnectionTest(input);

  const testState = (provider: string) => tests()[testStateKey(configKind(), provider)] ?? defaultConnectionTestState();
  const updateTest = (provider: string, patch: Partial<ConnectionTestState>) => {
    const key = testStateKey(configKind(), provider);
    setTests((prev) => ({ ...prev, [key]: { ...(prev[key] ?? defaultConnectionTestState()), ...patch } }));
  };
  const resetTest = (provider: string) => {
    const key = testStateKey(configKind(), provider);
    setTests((prev) => {
      const next = { ...prev };
      delete next[key];
      return next;
    });
  };

  createEffect(() => {
    if (!props.open) return;
    setLoading(true);
    setError("");
    setApiKeys({});
    setByteDanceCredentials({});
    setTests({});
    setVoicePreviews({});
    setSampleTexts({});
    setSimplePrompts({});
    setComplexPrompts({});
    setCustomVoiceIds({});
    setSelectedLanguages({});
    setGuideProviderId("");
    setGuideScenarioId(GOOGLE_TTS_SCENARIOS[0].id);
    setGuideSimplePrompts({});
    setGuideComplexPrompts({});
    setGuideInfoScenarioId("");
    setGuideSecondVoices({});
    previewAudio?.pause();
    previewAudio = null;
    loadConfig()
      .then(setConfig)
      .catch((err) => setError(err instanceof Error ? err.message : `Failed loading ${configKind()} config`))
      .finally(() => setLoading(false));
  });

  const setActiveProvider = (provider: string) => {
    setConfig((prev) => prev ? {
      ...prev,
      active_provider: provider,
      providers: prev.providers.map((item) => ({ ...item, active: item.provider === provider })),
    } : prev);
  };

  const updateProviderModel = (provider: string, model: string) => {
    const current = config()?.providers.find((item) => item.provider === provider);
    if (current?.model !== model) resetTest(provider);
    setConfig((prev) => prev ? {
      ...prev,
      providers: prev.providers.map((item) => item.provider === provider ? { ...item, model } : item),
    } : prev);
  };

  const updateVoice = (provider: string, model: string, voiceId: string) => {
    setConfig((prev) => prev ? {
      ...prev,
      providers: prev.providers.map((item) => item.provider === provider ? {
        ...item,
        selected_voice_by_model: {
          ...(item.selected_voice_by_model ?? {}),
          [model]: voiceId,
        },
      } : item),
    } : prev);
  };

  const selectedLanguage = (key: string) => selectedLanguages()[key] || "zh";

  const updateLanguage = (key: string, language: string) => {
    setSelectedLanguages((prev) => ({ ...prev, [key]: language }));
    setSampleTexts((prev) => ({ ...prev, [key]: defaultSampleText(language) }));
  };

  const orderedProviders = createMemo(() => {
    return [...(config()?.providers ?? [])];
  });

  const guideProvider = () => config()?.providers.find((provider) => provider.provider === guideProviderId()) ?? null;
  const guideScenario = () => GOOGLE_TTS_SCENARIOS.find((scenario) => scenario.id === guideScenarioId()) ?? GOOGLE_TTS_SCENARIOS[0];
  const guideInfoScenario = () => GOOGLE_TTS_SCENARIOS.find((scenario) => scenario.id === guideInfoScenarioId()) ?? null;

  const applyGuideScenario = (provider: Provider, scenarioId: string) => {
    const scenario = GOOGLE_TTS_SCENARIOS.find((item) => item.id === scenarioId) ?? GOOGLE_TTS_SCENARIOS[0];
    const model = selectedModel(provider);
    if (!model) return;
    const key = modelKey(provider.provider, model.model);
    setGuideScenarioId(scenario.id);
    setSelectedLanguages((prev) => ({ ...prev, [key]: scenario.defaultLanguage }));
    setGuideSimplePrompts((prev) => ({ ...prev, [key]: scenario.simplePrompt }));
    setGuideComplexPrompts((prev) => ({ ...prev, [key]: "" }));
    updateVoice(provider.provider, model.model, scenario.defaultVoice);
    if (scenario.secondVoice) setGuideSecondVoices((prev) => ({ ...prev, [key]: scenario.secondVoice! }));
  };

  const buildGuideComplexPrompt = (provider: Provider) => {
    const model = selectedModel(provider);
    if (!model) return;
    const key = modelKey(provider.provider, model.model);
    const scenario = guideScenario();
    const simple = guideSimplePrompts()[key] || scenario.simplePrompt;
    const language = selectedLanguage(key) || scenario.defaultLanguage;
    setGuideComplexPrompts((prev) => ({ ...prev, [key]: scenario.buildComplexPrompt(simple, language) }));
  };

  const updateByteDanceCredential = (provider: string, patch: Partial<ByteDanceCredentialDraft>) => {
    setByteDanceCredentials((prev) => ({
      ...prev,
      [provider]: { appId: "", accessToken: "", ...(prev[provider] ?? {}), ...patch },
    }));
  };

  const providerUnsavedCredentials = (provider: Provider) => {
    if (provider.provider === "bytedance") {
      const draft = byteDanceCredentials()[provider.provider];
      return Boolean((draft?.appId || "").trim() || (draft?.accessToken || "").trim());
    }
    return Boolean((apiKeys()[provider.provider] ?? "").trim());
  };

  const providerCredentialPayload = (provider: Provider) => {
    if (provider.provider === "bytedance") return byteDanceCredentialPayload(byteDanceCredentials()[provider.provider]);
    return apiKeys()[provider.provider] ?? "";
  };

  const save = async () => {
    const current = config();
    if (!current) return;
    const incompleteByteDanceProvider = current.providers.find((item) => item.provider === "bytedance" && providerCredentialPayload(item) === null);
    if (incompleteByteDanceProvider) {
      setError("ByteDance Volcano TTS requires both App ID and Access Token. Leave both blank to keep the saved credentials.");
      return;
    }
    setSaving(true);
    setError("");
    try {
      const res = await saveConfig({
        active_provider: current.active_provider,
        providers: current.providers.map((item) => ({
          provider: item.provider,
          model: item.model,
          api_key: providerCredentialPayload(item) || "",
          enabled: item.enabled,
          selected_voice_by_model: item.selected_voice_by_model ?? {},
          extra: providerExtras()[item.provider] ?? {},
        })),
      });
      setConfig(res);
      setApiKeys({});
      setByteDanceCredentials({});
      setProviderExtras({});
      setTests({});
      props.onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : `Failed saving ${configKind()} config`);
    } finally {
      setSaving(false);
    }
  };

  const runTest = async (provider: string, model: string) => {
    updateTest(provider, { status: "testing", message: "Testing...", detail: "", expanded: false });
    try {
      const res = await testConnection({ provider, model });
      updateTest(provider, { status: res.ok ? "success" : "failed", message: res.message, detail: res.detail || "", expanded: !res.ok });
    } catch (err) {
      updateTest(provider, { status: "failed", message: "Connection failed", detail: err instanceof Error ? err.message : "Unknown error", expanded: true });
    }
  };

  const generatePreview = async (provider: Provider, override?: { sampleText?: string; complexPrompt?: string; multiSpeaker?: boolean; secondVoiceId?: string; speaker1?: string; speaker2?: string; voiceId?: string; language?: string }) => {
    const model = selectedModel(provider);
    const mode = voiceModeFor(provider);
    const keyBase = model ? modelKey(provider.provider, model.model) : "";
    const configuredVoiceId = selectedVoiceId(provider);
    const voiceId = override?.voiceId || (mode === "custom_voice_id" ? (customVoiceIds()[keyBase]?.trim() || configuredVoiceId) : configuredVoiceId);
    const voice = model?.voices?.find((item) => item.voice_id === voiceId);
    if (!model || !voiceId) return;
    const key = previewKey(provider.provider, model.model, voiceId);
    setVoicePreviews((prev) => ({ ...prev, [key]: { status: "generating", audioUrl: prev[key]?.audioUrl || "", error: "" } }));
    try {
      const res = await modelConfigApi.ttsVoicePreview({
        provider: provider.provider,
        model: model.model,
        config_kind: configKind(),
        voice_id: voiceId,
        second_voice_id: override?.secondVoiceId || "",
        speaker_1: override?.speaker1 || "Speaker1",
        speaker_2: override?.speaker2 || "Speaker2",
        sample_text: override?.sampleText || sampleTexts()[keyBase] || voice?.sample_text || defaultSampleText(selectedLanguage(keyBase)),
        simple_prompt: simplePrompts()[keyBase] || "",
        complex_prompt: override?.complexPrompt || complexPrompts()[keyBase] || "",
        multi_speaker: Boolean(override?.multiSpeaker),
        language: override?.language || selectedLanguage(keyBase),
      });
      setVoicePreviews((prev) => ({ ...prev, [key]: { status: "ready", audioUrl: res.audio_url, error: "" } }));
    } catch (err) {
      setVoicePreviews((prev) => ({ ...prev, [key]: { status: "failed", audioUrl: "", error: err instanceof Error ? err.message : "Preview generation failed" } }));
    }
  };

  const playPreview = async (provider: Provider, override?: { sampleText?: string; complexPrompt?: string; multiSpeaker?: boolean; secondVoiceId?: string; speaker1?: string; speaker2?: string; voiceId?: string; language?: string }) => {
    const model = selectedModel(provider);
    const mode = voiceModeFor(provider);
    const keyBase = model ? modelKey(provider.provider, model.model) : "";
    const configuredVoiceId = selectedVoiceId(provider);
    const voiceId = override?.voiceId || (mode === "custom_voice_id" ? (customVoiceIds()[keyBase]?.trim() || configuredVoiceId) : configuredVoiceId);
    if (!model || !voiceId) return;
    const key = previewKey(provider.provider, model.model, voiceId);
    await generatePreview(provider, override);
    const state = voicePreviews()[key];
    if (!state?.audioUrl) return;
    previewAudio?.pause();
    previewAudio = new Audio(state.audioUrl);
    setVoicePreviews((prev) => ({ ...prev, [key]: { ...state!, status: "playing" } }));
    previewAudio.onended = () => setVoicePreviews((prev): Record<string, TTSPreviewState> => ({ ...prev, [key]: { ...(prev[key] ?? state!), status: "ready" } }));
    try {
      await previewAudio.play();
    } catch (err) {
      setVoicePreviews((prev) => ({ ...prev, [key]: { ...(prev[key] ?? state!), status: "failed", error: err instanceof Error ? err.message : "Playback failed" } }));
    }
  };

  return (
    <Show when={props.open}>
      <div class="drawer-backdrop" onClick={props.onClose} />
      <div class="env-dialog media-config-dialog">
        <div class="env-dialog-head">
          <h3>{modalTitle()}</h3>
          <button class="icon-action" type="button" title="Close" onClick={props.onClose}><CloseIcon /></button>
        </div>
        <Show when={error()}>
          <div class="banner bad">{error()}</div>
        </Show>
        <Show when={!loading() && config()} fallback={<div class="message-panel"><p class="helper">{loadingLabel()}</p></div>}>
          <div class="media-provider-grid">
            <For each={orderedProviders()}>{(provider) => {
              const selected = () => selectedModel(provider);
              const keyBase = () => selected() ? modelKey(provider.provider, selected()!.model) : "";
              const mode = () => voiceModeFor(provider);
              const configuredVoiceId = () => selectedVoiceId(provider);
              const customVoiceId = () => customVoiceIds()[keyBase()] ?? "";
              const voiceId = () => mode() === "custom_voice_id" ? (customVoiceId().trim() || configuredVoiceId()) : configuredVoiceId();
              const selectedVoice = () => selected()?.voices?.find((voice) => voice.voice_id === voiceId()) ?? selected()?.voices?.[0];
              const currentPreviewKey = () => selected() ? previewKey(provider.provider, selected()!.model, voiceId()) : "";
              const preview = () => currentPreviewKey() ? voicePreviews()[currentPreviewKey()] : undefined;
              const language = () => selectedLanguage(keyBase());
              const sampleText = () => sampleTexts()[keyBase()] || (provider.provider === "google" ? defaultSampleText(language()) : selectedVoice()?.sample_text || DEFAULT_SAMPLE_TEXT);
              return (
                <section class={`media-provider-card ${provider.active ? "active" : ""}`} onClick={() => setActiveProvider(provider.provider)}>
                  <div class="media-provider-head">
                    <h4>{provider.provider_label}</h4>
                    <ConnectionTestControl
                      state={() => testState(provider.provider)}
                      model={provider.model}
                      unsaved={providerUnsavedCredentials(provider)}
                      onTest={() => runTest(provider.provider, provider.model)}
                      onToggle={() => updateTest(provider.provider, { expanded: !testState(provider.provider).expanded })}
                    />
                    <span class={`media-provider-status ${provider.active ? "active" : ""}`}>{provider.active ? "ACTIVE" : "Set Active"}</span>
                  </div>
                  <div class="media-model-chip-list" onClick={(event) => event.stopPropagation()}>
                    <For each={provider.models}>{(model) => (
                      <button class={`media-model-chip ${provider.model === model.model ? "selected" : ""} ${model.description ? "has-tooltip" : ""}`} type="button" title={model.description || model.label} data-tooltip={model.description || ""} onClick={() => {
                        setActiveProvider(provider.provider);
                        updateProviderModel(provider.provider, model.model);
                      }}>
                        {model.label}
                      </button>
                    )}</For>
                  </div>
                  <div class="tts-voice-panel" onClick={(event) => event.stopPropagation()}>
                    <div class={`tts-voice-head ${provider.provider === "google" || provider.provider === "xai" ? "with-language" : ""}`}>
                      <label class="openflow-field tts-voice-select">
                        <span>Default Voice</span>
                        <select value={voiceId()} onChange={(event) => selected() && updateVoice(provider.provider, selected()!.model, event.currentTarget.value)}>
                          <For each={visibleVoices(provider)}>{(voice) => <option value={voice.voice_id}>{voice.label}</option>}</For>
                        </select>
                      </label>
                      <Show when={provider.provider === "google" || provider.provider === "xai"}>
                        <label class="openflow-field tts-language-select">
                          <span>Language</span>
                          <select value={language()} onChange={(event) => updateLanguage(keyBase(), event.currentTarget.value)}>
                            <For each={provider.provider === "xai" ? XAI_LANGUAGE_OPTIONS : LANGUAGE_OPTIONS}>{(option) => <option value={option.value}>{option.label}</option>}</For>
                          </select>
                        </label>
                      </Show>
                      <button class="tts-play-icon-button" type="button" title={preview()?.status === "generating" ? "Generating preview" : "Play preview"} aria-label={preview()?.status === "generating" ? "Generating preview" : "Play preview"} disabled={preview()?.status === "generating" || !selected() || !voiceId()} onClick={() => void playPreview(provider)}>
                        <PlayIcon />
                      </button>
                    </div>
                    <Show when={mode() === "custom_voice_id"}>
                      <label class="openflow-field tts-full-row">
                        <span>Custom Voice ID</span>
                        <input value={customVoiceId()} placeholder={configuredVoiceId() || "Paste provider voice id"} onInput={(event) => setCustomVoiceIds((prev) => ({ ...prev, [keyBase()]: event.currentTarget.value }))} />
                      </label>
                    </Show>
                    <Show when={mode() === "instruct_prompt"}>
                      <div class="tts-instruct-grid">
                        <label class="openflow-field">
                          <span>Simple Voice Prompt</span>
                          <textarea rows={3} value={simplePrompts()[keyBase()] || ""} placeholder="例如：成熟女性，温柔但有权威感，适合医美老板私域转化视频" onInput={(event) => setSimplePrompts((prev) => ({ ...prev, [keyBase()]: event.currentTarget.value }))} />
                        </label>
                        <label class="openflow-field">
                          <span>Complex Voice Prompt</span>
                          <textarea rows={3} value={complexPrompts()[keyBase()] || ""} placeholder="点击生成复杂提示词后用于验证 instruct 效果" onInput={(event) => setComplexPrompts((prev) => ({ ...prev, [keyBase()]: event.currentTarget.value }))} />
                        </label>
                      </div>
                      <button class="secondary tts-prompt-build" type="button" onClick={() => setComplexPrompts((prev) => ({ ...prev, [keyBase()]: buildComplexPrompt(simplePrompts()[keyBase()] || "", provider.provider, selected()?.model || "") }))}>
                        Generate Complex Prompt
                      </button>
                    </Show>
                    <label class="openflow-field tts-full-row">
                      <span>{mode() === "custom_voice_id" ? "Provider Example / Test Text" : "Test Text"}</span>
                      <textarea rows={3} value={sampleText()} onInput={(event) => setSampleTexts((prev) => ({ ...prev, [keyBase()]: event.currentTarget.value }))} />
                    </label>
                    <Show when={preview()?.status === "failed"}>
                      <p class="tts-preview-error">{preview()?.error}</p>
                    </Show>
                  </div>
                  <div class="media-key-row" onClick={(event) => event.stopPropagation()}>
                    <Show when={provider.provider === "bytedance"} fallback={
                      <label class="openflow-field">
                        <span>API Key</span>
                        <input type="password" placeholder={provider.has_api_key ? "Leave blank to keep existing key" : "Paste API Key"} value={apiKeys()[provider.provider] ?? ""} onInput={(event) => setApiKeys((prev) => ({ ...prev, [provider.provider]: event.currentTarget.value }))} />
                      </label>
                    }>
                      <div class="tts-bytedance-credentials">
                        <label class="openflow-field">
                          <span>App ID</span>
                          <input autocomplete="off" placeholder={provider.has_api_key ? "Leave blank to keep saved App ID" : "Paste Volcano App ID"} value={byteDanceCredentials()[provider.provider]?.appId ?? ""} onInput={(event) => updateByteDanceCredential(provider.provider, { appId: event.currentTarget.value })} />
                        </label>
                        <label class="openflow-field">
                          <span>Access Token</span>
                          <input type="password" autocomplete="off" placeholder={provider.has_api_key ? "Leave blank to keep saved token" : "Paste Volcano Access Token"} value={byteDanceCredentials()[provider.provider]?.accessToken ?? ""} onInput={(event) => updateByteDanceCredential(provider.provider, { accessToken: event.currentTarget.value })} />
                        </label>
                      </div>
                    </Show>
                    <div class="media-key-status">
                      <strong>{provider.provider === "bytedance" ? (provider.has_api_key ? "Credentials saved" : "Credentials missing") : (provider.has_api_key ? "API Key saved" : "API Key missing")}</strong>
                    </div>
                  </div>
                  <Show when={provider.extra_json && Object.prototype.hasOwnProperty.call(provider.extra_json, "group_id")}>
                    <div class="media-key-row" onClick={(event) => event.stopPropagation()}>
                      <label class="openflow-field">
                        <span>Group ID</span>
                        <input
                          autocomplete="off"
                          placeholder="Required account group identifier"
                          value={providerExtras()[provider.provider]?.group_id ?? String(provider.extra_json?.group_id ?? "")}
                          onInput={(event) => setProviderExtras((prev) => ({ ...prev, [provider.provider]: { ...(prev[provider.provider] ?? {}), group_id: event.currentTarget.value } }))}
                        />
                      </label>
                    </div>
                  </Show>
                  <div class="media-provider-foot">
                    <a href={provider.docs_url} target="_blank" rel="noreferrer" onClick={(event) => event.stopPropagation()}>Docs</a>
                    <Show when={provider.voice_guide_url}>
                      <button type="button" onClick={(event) => {
                        event.stopPropagation();
                        if (provider.provider === "google") applyGuideScenario(provider, guideScenarioId());
                        setGuideProviderId(provider.provider);
                      }}>Voice Guide</button>
                    </Show>
                  </div>
                </section>
              );
            }}</For>
          </div>
          <div class="asr-config-actions media-config-actions">
            <button class="secondary" type="button" onClick={props.onClose}>Cancel</button>
            <button type="button" disabled={saving() || !config()} onClick={() => void save()}>{saving() ? "Saving..." : saveButtonLabel()}</button>
          </div>
        </Show>
      </div>
      <Show when={guideProvider()?.provider === "xai" && guideProvider()}>{(provider) => (
        <XaiVoiceGuide
          provider={provider()}
          onClose={() => setGuideProviderId("")}
          selectedModel={selectedModel}
          selectedVoiceId={selectedVoiceId}
          visibleVoices={visibleVoices}
          modelKey={modelKey}
          previewKey={previewKey}
          updateVoice={updateVoice}
          voicePreviews={voicePreviews}
          playPreview={playPreview}
          selectedLanguage={selectedLanguage}
          updateLanguage={updateLanguage}
        />
      )}</Show>
      <Show when={guideProvider()?.provider === "qwen" && guideProvider()}>{(provider) => (
        <QwenVoiceGuide
          provider={provider()}
          onClose={() => setGuideProviderId("")}
          selectedModel={selectedModel}
          selectedVoiceId={selectedVoiceId}
          visibleVoices={visibleVoices}
          modelKey={modelKey}
          previewKey={previewKey}
          updateVoice={updateVoice}
          voicePreviews={voicePreviews}
          playPreview={playPreview}
          selectedLanguage={selectedLanguage}
          updateLanguage={updateLanguage}
        />
      )}</Show>
      <Show when={guideProvider()?.provider !== "xai" && guideProvider()?.provider !== "qwen" && guideProvider()}>{(provider) => {
        const selected = () => selectedModel(provider());
        const keyBase = () => selected() ? modelKey(provider().provider, selected()!.model) : "";
        const scenario = () => guideScenario();
        const voiceId = () => selectedVoiceId(provider());
        const secondVoiceId = () => guideSecondVoices()[keyBase()] || scenario().secondVoice || "Puck";
        const currentPreviewKey = () => selected() ? previewKey(provider().provider, selected()!.model, voiceId()) : "";
        const preview = () => currentPreviewKey() ? voicePreviews()[currentPreviewKey()] : undefined;
        const language = () => selectedLanguage(keyBase());
        const simplePrompt = () => guideSimplePrompts()[keyBase()] || scenario().simplePrompt;
        const complexPrompt = () => guideComplexPrompts()[keyBase()] || "";
        const previewText = () => complexPrompt().trim() || simplePrompt();
        return (
          <>
            <div class="drawer-backdrop tts-guide-backdrop" onClick={() => setGuideProviderId("")} />
            <div class="env-dialog tts-guide-dialog" onClick={(event) => event.stopPropagation()}>
              <div class="env-dialog-head">
                <h3>{provider().provider_label} Scenario Lab</h3>
                <button class="icon-action" type="button" title="Close" onClick={() => setGuideProviderId("")}><CloseIcon /></button>
              </div>
              <div class="tts-guide-tool">
                <div class="tts-scenario-row">
                  <label class="openflow-field">
                    <span>Scenario</span>
                    <select value={scenario().id} onChange={(event) => applyGuideScenario(provider(), event.currentTarget.value)}>
                      <For each={GOOGLE_TTS_SCENARIOS}>{(item) => <option value={item.id}>{item.label}</option>}</For>
                    </select>
                  </label>
                  <button class="tts-info-button" type="button" title="Scenario information" aria-label="Scenario information" onClick={() => setGuideInfoScenarioId(scenario().id)}>i</button>
                </div>
                <div class={`tts-guide-controls ${scenario().multiSpeaker ? "multi-speaker" : ""}`}>
                  <label class="openflow-field tts-voice-select">
                    <span>{scenario().multiSpeaker ? scenario().speaker1 || "Speaker 1" : "Voice"}</span>
                    <select value={voiceId()} onChange={(event) => selected() && updateVoice(provider().provider, selected()!.model, event.currentTarget.value)}>
                      <For each={visibleVoices(provider())}>{(voice) => <option value={voice.voice_id}>{voice.label}</option>}</For>
                    </select>
                  </label>
                  <Show when={scenario().multiSpeaker}>
                    <label class="openflow-field tts-voice-select">
                      <span>{scenario().speaker2 || "Speaker 2"}</span>
                      <select value={secondVoiceId()} onChange={(event) => setGuideSecondVoices((prev) => ({ ...prev, [keyBase()]: event.currentTarget.value }))}>
                        <For each={visibleVoices(provider())}>{(voice) => <option value={voice.voice_id}>{voice.label}</option>}</For>
                      </select>
                    </label>
                  </Show>
                  <label class="openflow-field tts-language-select">
                    <span>Language</span>
                    <select value={language()} onChange={(event) => updateLanguage(keyBase(), event.currentTarget.value)}>
                      <For each={LANGUAGE_OPTIONS}>{(option) => <option value={option.value}>{option.label}</option>}</For>
                    </select>
                  </label>
                  <button class="tts-play-icon-button" type="button" title="Play preview" aria-label="Play preview" disabled={preview()?.status === "generating" || !selected() || !voiceId()} onClick={() => void playPreview(provider(), {
                    voiceId: voiceId(),
                    sampleText: previewText(),
                    complexPrompt: complexPrompt(),
                    multiSpeaker: Boolean(scenario().multiSpeaker),
                    secondVoiceId: secondVoiceId(),
                    speaker1: scenario().speaker1 || "Speaker1",
                    speaker2: scenario().speaker2 || "Speaker2",
                  })}>
                    <PlayIcon />
                  </button>
                </div>
                <label class="openflow-field tts-full-row">
                  <span>Simple Prompt</span>
                  <textarea rows={5} value={simplePrompt()} onInput={(event) => setGuideSimplePrompts((prev) => ({ ...prev, [keyBase()]: event.currentTarget.value }))} />
                </label>
                <div class="tts-guide-actions">
                  <button class="secondary" type="button" onClick={() => buildGuideComplexPrompt(provider())}>Generate Complex Prompt</button>
                  <button type="button" disabled={preview()?.status === "generating" || !selected() || !voiceId()} onClick={() => void playPreview(provider(), {
                    voiceId: voiceId(),
                    sampleText: previewText(),
                    complexPrompt: complexPrompt(),
                    multiSpeaker: Boolean(scenario().multiSpeaker),
                    secondVoiceId: secondVoiceId(),
                    speaker1: scenario().speaker1 || "Speaker1",
                    speaker2: scenario().speaker2 || "Speaker2",
                  })}>{preview()?.status === "generating" ? "Generating..." : preview()?.status === "playing" ? "Playing..." : "Play Preview"}</button>
                </div>
                <label class="openflow-field tts-full-row">
                  <span>Complex Prompt (Markdown)</span>
                  <textarea class="tts-complex-prompt" rows={10} placeholder="Click Generate Complex Prompt, then edit before previewing." value={complexPrompt()} onInput={(event) => setGuideComplexPrompts((prev) => ({ ...prev, [keyBase()]: event.currentTarget.value }))} />
                </label>
                <div class="tts-guide-links">
                  <a href={provider().docs_url} target="_blank" rel="noreferrer">Provider Docs</a>
                  <Show when={provider().provider === "google"}>
                    <a href={GOOGLE_VOICE_LIBRARY_URL} target="_blank" rel="noreferrer">AI Studio Voice Library</a>
                  </Show>
                </div>
                <Show when={preview()?.status === "failed"}>
                  <p class="tts-preview-error">{preview()?.error}</p>
                </Show>
              </div>
            </div>
          </>
        );
      }}</Show>
      <Show when={guideInfoScenario()}>{(scenario) => (
        <>
          <div class="drawer-backdrop tts-info-backdrop" onClick={() => setGuideInfoScenarioId("")} />
          <div class="env-dialog tts-info-dialog" onClick={(event) => event.stopPropagation()}>
            <div class="env-dialog-head">
              <h3>{scenario().infoTitle}</h3>
              <button class="icon-action" type="button" title="Close" onClick={() => setGuideInfoScenarioId("")}><CloseIcon /></button>
            </div>
            <p>{scenario().infoBodyZh}</p>
            <Show when={GOOGLE_AUDIO_TAG_SCENARIO_IDS.has(scenario().id)}>
              <div class="tts-audio-tag-table">
                <For each={googleAudioTagsForScenario(scenario().id)}>{(item) => (
                  <div>
                    <code>{item.tag}</code>
                    <span>{item.zh}</span>
                  </div>
                )}</For>
              </div>
            </Show>
            <Show when={scenario().id === "accent-control"}>
              <div class="tts-accent-table">
                <For each={GOOGLE_DOCUMENTED_ACCENTS}>{(item) => (
                  <div>
                    <strong>{item.country}</strong>
                    <span>{item.region}</span>
                    <code>{item.prompt}</code>
                    <p>{item.zh}</p>
                  </div>
                )}</For>
              </div>
            </Show>
            <div class="tts-info-tags">
              <For each={scenario().verifies}>{(item) => <span>{item}</span>}</For>
            </div>
          </div>
        </>
      )}</Show>
    </Show>
  );
}
