import { For, Show, createSignal } from "solid-js";
import { CloseIcon, PlayIcon } from "../shared/icons";
import type { MediaModelConfigResponse, TTSPreviewState } from "../shared/types";

type Provider = MediaModelConfigResponse["providers"][number];

type XAITTSScenario = {
  id: string;
  label: string;
  defaultVoice: string;
  defaultLanguage: string;
  usesCustomVoice?: boolean;
  simplePrompt: string;
  infoTitle: string;
  infoBodyZh: string;
  verifies: string[];
  buildComplexPrompt: (simplePrompt: string, customVoiceId?: string) => string;
};

type PreviewOptions = {
  sampleText?: string;
  complexPrompt?: string;
  voiceId?: string;
};

const XAI_CUSTOM_VOICE_CONSOLE_URL = "https://console.x.ai/";

export const XAI_LANGUAGE_OPTIONS = [
  { value: "auto", label: "Auto" },
  { value: "zh", label: "中文" },
  { value: "en", label: "English" },
  { value: "pt-BR", label: "Português BR" },
  { value: "pt-PT", label: "Português PT" },
  { value: "es-MX", label: "Español MX" },
  { value: "es-ES", label: "Español ES" },
  { value: "fr", label: "Français" },
  { value: "de", label: "Deutsch" },
  { value: "ja", label: "日本語" },
  { value: "ko", label: "한국어" },
  { value: "it", label: "Italiano" },
  { value: "ru", label: "Русский" },
  { value: "tr", label: "Türkçe" },
  { value: "vi", label: "Tiếng Việt" },
  { value: "id", label: "Indonesia" },
  { value: "hi", label: "हिन्दी" },
  { value: "bn", label: "বাংলা" },
  { value: "ar-EG", label: "Arabic EG" },
  { value: "ar-SA", label: "Arabic SA" },
  { value: "ar-AE", label: "Arabic AE" },
];

const XAI_TTS_SCENARIOS: XAITTSScenario[] = [
  {
    id: "xai-basic",
    label: "基础朗读",
    defaultVoice: "eve",
    defaultLanguage: "zh",
    simplePrompt: "你好，欢迎使用 xAI Text to Speech。我们正在测试中文基础朗读、清晰度和自然度。",
    infoTitle: "基础朗读",
    infoBodyZh: "验证 xAI TTS 的基础请求：text、voice_id、language。文档说明 response body 是原始音频字节，可直接保存或播放。",
    verifies: ["text", "voice_id", "language", "基础音频生成"],
    buildComplexPrompt: (simple) => simple,
  },
  {
    id: "xai-voices",
    label: "内置声音对比",
    defaultVoice: "rex",
    defaultLanguage: "zh",
    simplePrompt: "这是一段商务汇报风格的旁白。请保持专业、清晰、有条理，适合产品演示和企业培训。",
    infoTitle: "内置声音对比",
    infoBodyZh: "验证 xAI Voice Library 和 GET /v1/tts/voices 返回的内置声音。这里优先列出多语言、中文和英语音色，便于在中英文内容里快速比较。",
    verifies: ["多语言 voice", "中文 voice", "英文 voice", "业务场景匹配"],
    buildComplexPrompt: (simple) => simple,
  },
  {
    id: "xai-language",
    label: "语言选择/自动检测",
    defaultVoice: "ara",
    defaultLanguage: "auto",
    simplePrompt: "你好，这是中文。Hello, this is English. 这段文本用于测试 auto 语言检测和混合语言朗读。",
    infoTitle: "语言选择/自动检测",
    infoBodyZh: "验证 xAI 支持的 BCP-47 language 参数。文档支持 auto 自动检测，也可显式传入 zh、en、pt-BR、es-MX 等语言代码。",
    verifies: ["auto", "BCP-47", "混合语言"],
    buildComplexPrompt: (simple) => simple,
  },
  {
    id: "xai-inline-tags",
    label: "Inline Speech Tags",
    defaultVoice: "eve",
    defaultLanguage: "en",
    simplePrompt: "So I walked in and [pause] there it was. [laugh] I honestly could not believe it! [sigh] What a day.",
    infoTitle: "Inline Speech Tags",
    infoBodyZh: "验证 xAI 的 inline tags，例如 [pause]、[laugh]、[sigh]。这些标签插入在文本中某个位置，用来产生停顿、笑声、呼吸等表达。",
    verifies: ["inline tags", "pause", "laugh", "sigh"],
    buildComplexPrompt: (simple) => simple,
  },
  {
    id: "xai-wrapping-tags",
    label: "Wrapping Speech Tags",
    defaultVoice: "eve",
    defaultLanguage: "en",
    simplePrompt: "I need to tell you something. <whisper>It is a secret.</whisper> Pretty cool, right? <loud>Now listen carefully.</loud>",
    infoTitle: "Wrapping Speech Tags",
    infoBodyZh: "验证 xAI 的 wrapping tags，例如 <whisper>text</whisper>、<loud>text</loud>。这类标签包裹一段文本，改变这段文本的朗读方式。",
    verifies: ["wrapping tags", "whisper", "loud", "局部风格"],
    buildComplexPrompt: (simple) => simple,
  },
  {
    id: "xai-custom-voice",
    label: "Custom Voice ID",
    defaultVoice: "eve",
    defaultLanguage: "en",
    usesCustomVoice: true,
    simplePrompt: "Hello! This is my custom voice test. If the custom voice ID is valid, this sentence should use that cloned voice.",
    infoTitle: "Custom Voice ID",
    infoBodyZh: "验证 xAI Custom Voices。文档说明可通过 Custom Voices API 或控制台创建声音，然后复制 Voice ID，并将它作为 voice_id 传给 TTS 接口。",
    verifies: ["custom voice_id", "克隆声音", "控制台复制 Voice ID"],
    buildComplexPrompt: (simple, customVoiceId) => `${simple}\n\nCustom voice_id to test: ${customVoiceId || "请填写 Custom Voice ID"}`,
  },
];

const XAI_INLINE_TAGS = [
  { tag: "[pause]", zh: "停顿" },
  { tag: "[long-pause]", zh: "长停顿" },
  { tag: "[hum-tune]", zh: "哼唱旋律" },
  { tag: "[laugh]", zh: "笑" },
  { tag: "[chuckle]", zh: "轻笑" },
  { tag: "[giggle]", zh: "咯咯笑" },
  { tag: "[cry]", zh: "哭" },
  { tag: "[tsk]", zh: "啧声" },
  { tag: "[tongue-click]", zh: "弹舌声" },
  { tag: "[lip-smack]", zh: "咂嘴声" },
  { tag: "[breath]", zh: "呼吸声" },
  { tag: "[inhale]", zh: "吸气" },
  { tag: "[exhale]", zh: "呼气" },
  { tag: "[sigh]", zh: "叹气" },
];

const XAI_WRAPPING_TAGS = [
  { tag: "<soft>...</soft>", zh: "轻柔" },
  { tag: "<whisper>...</whisper>", zh: "低语" },
  { tag: "<loud>...</loud>", zh: "大声" },
  { tag: "<build-intensity>...</build-intensity>", zh: "逐渐增强" },
  { tag: "<decrease-intensity>...</decrease-intensity>", zh: "逐渐减弱" },
  { tag: "<higher-pitch>...</higher-pitch>", zh: "更高音高" },
  { tag: "<lower-pitch>...</lower-pitch>", zh: "更低音高" },
  { tag: "<slow>...</slow>", zh: "慢速" },
  { tag: "<fast>...</fast>", zh: "快速" },
  { tag: "<sing-song>...</sing-song>", zh: "吟唱式" },
  { tag: "<singing>...</singing>", zh: "歌唱" },
  { tag: "<laugh-speak>...</laugh-speak>", zh: "带笑说话" },
  { tag: "<emphasis>...</emphasis>", zh: "强调" },
];

const XAI_VOICE_GUIDE = [
  { voice: "ara", tone: "multilingual / female", zh: "多语言温暖女声，适合客服、对话和旁白" },
  { voice: "eve", tone: "multilingual / female", zh: "多语言活力女声，适合演示、公告和轻快内容" },
  { voice: "leo", tone: "multilingual / male", zh: "多语言权威男声，适合说明、教学和指令" },
  { voice: "rex", tone: "multilingual / male", zh: "多语言商务男声，适合商务演示和企业沟通" },
  { voice: "sal", tone: "multilingual / male", zh: "多语言均衡男声，适合多种通用内容" },
  { voice: "Hui / e521cc67", tone: "zh / female / young", zh: "中文年轻女声，适合亲和型介绍和轻快旁白" },
  { voice: "Wei / 9ab26871", tone: "zh / male / young", zh: "中文年轻男声，适合清爽讲解和短视频旁白" },
  { voice: "Yang / 6997b0ec", tone: "zh / male / middle-aged", zh: "中文成熟男声，适合稳重说明和商业内容" },
  { voice: "Mei / 09b02491", tone: "zh / female / young", zh: "中文年轻女声，适合温和介绍和生活化表达" },
  { voice: "Emma / d11249e6", tone: "en-US / female / old", zh: "美式英语资深女声，适合叙事和可信说明" },
  { voice: "Liam / 6a41d324", tone: "en-US / male / middle-aged", zh: "美式英语成熟男声，适合产品说明和企业内容" },
  { voice: "Henry / f15c6a6a", tone: "en-GB / male / middle-aged", zh: "英式英语成熟男声，适合正式叙述和品牌内容" },
  { voice: "Olivia / bedd6226", tone: "en-GB / female / young", zh: "英式英语年轻女声，适合轻快介绍和清晰播报" },
  { voice: "Sean / a7b78b05", tone: "en-IE / male / middle-aged", zh: "爱尔兰英语成熟男声，适合自然对话和地区化表达" },
  { voice: "Niamh / 355dca53", tone: "en-IE / female / middle-aged", zh: "爱尔兰英语成熟女声，适合温和叙事和本地化内容" },
  { voice: "Marc / 5d695b41", tone: "en-ZA / male / middle-aged", zh: "南非英语成熟男声，适合地区化说明和访谈感内容" },
  { voice: "Thandi / 135ff7ec", tone: "en-ZA / female / middle-aged", zh: "南非英语成熟女声，适合自然叙事和本地化表达" },
];

export function XaiVoiceGuide(props: {
  provider: Provider;
  onClose: () => void;
  selectedModel: (provider: Provider) => Provider["models"][number] | null;
  selectedVoiceId: (provider: Provider) => string;
  visibleVoices: (provider: Provider) => NonNullable<Provider["models"][number]["voices"]>;
  modelKey: (provider: string, model: string) => string;
  previewKey: (provider: string, model: string, voiceId: string) => string;
  updateVoice: (provider: string, model: string, voiceId: string) => void;
  voicePreviews: () => Record<string, TTSPreviewState>;
  playPreview: (provider: Provider, override?: PreviewOptions) => Promise<void>;
  selectedLanguage: (key: string) => string;
  updateLanguage: (key: string, language: string) => void;
}) {
  const [scenarioId, setScenarioId] = createSignal(XAI_TTS_SCENARIOS[0].id);
  const [simplePrompts, setSimplePrompts] = createSignal<Record<string, string>>({});
  const [complexPrompts, setComplexPrompts] = createSignal<Record<string, string>>({});
  const [customVoiceIds, setCustomVoiceIds] = createSignal<Record<string, string>>({});
  const [infoScenarioId, setInfoScenarioId] = createSignal("");

  const selected = () => props.selectedModel(props.provider);
  const keyBase = () => selected() ? props.modelKey(props.provider.provider, selected()!.model) : "";
  const scenario = () => XAI_TTS_SCENARIOS.find((item) => item.id === scenarioId()) ?? XAI_TTS_SCENARIOS[0];
  const infoScenario = () => XAI_TTS_SCENARIOS.find((item) => item.id === infoScenarioId()) ?? null;
  const voiceId = () => props.selectedVoiceId(props.provider);
  const customVoiceId = () => customVoiceIds()[keyBase()]?.trim() || "";
  const effectiveVoiceId = () => scenario().usesCustomVoice && customVoiceId() ? customVoiceId() : voiceId();
  const preview = () => selected() ? props.voicePreviews()[props.previewKey(props.provider.provider, selected()!.model, effectiveVoiceId())] : undefined;
  const language = () => props.selectedLanguage(keyBase()) || scenario().defaultLanguage;
  const simplePrompt = () => simplePrompts()[keyBase()] || scenario().simplePrompt;
  const complexPrompt = () => complexPrompts()[keyBase()] || "";
  const previewText = () => complexPrompt().trim() || simplePrompt();

  const applyScenario = (nextId: string) => {
    const next = XAI_TTS_SCENARIOS.find((item) => item.id === nextId) ?? XAI_TTS_SCENARIOS[0];
    const model = selected();
    if (!model) return;
    const key = props.modelKey(props.provider.provider, model.model);
    setScenarioId(next.id);
    props.updateLanguage(key, next.defaultLanguage);
    setSimplePrompts((prev) => ({ ...prev, [key]: next.simplePrompt }));
    setComplexPrompts((prev) => ({ ...prev, [key]: "" }));
    props.updateVoice(props.provider.provider, model.model, next.defaultVoice);
  };

  const buildComplexPrompt = () => {
    setComplexPrompts((prev) => ({
      ...prev,
      [keyBase()]: scenario().buildComplexPrompt(simplePrompt(), customVoiceId()),
    }));
  };

  const play = () => props.playPreview(props.provider, {
    voiceId: effectiveVoiceId(),
    sampleText: previewText(),
    complexPrompt: complexPrompt(),
  });

  return (
    <>
      <div class="drawer-backdrop tts-guide-backdrop" onClick={props.onClose} />
      <div class="env-dialog tts-guide-dialog" onClick={(event) => event.stopPropagation()}>
        <div class="env-dialog-head">
          <h3>xAI Voice Guide</h3>
          <button class="icon-action" type="button" title="Close" onClick={props.onClose}><CloseIcon /></button>
        </div>
        <div class="tts-guide-tool">
          <div class="tts-scenario-row">
            <label class="openflow-field">
              <span>Scenario</span>
              <select value={scenario().id} onChange={(event) => applyScenario(event.currentTarget.value)}>
                <For each={XAI_TTS_SCENARIOS}>{(item) => <option value={item.id}>{item.label}</option>}</For>
              </select>
            </label>
            <button class="tts-info-button" type="button" title="Scenario information" aria-label="Scenario information" onClick={() => setInfoScenarioId(scenario().id)}>i</button>
          </div>
          <div class="tts-guide-controls">
            <label class="openflow-field tts-voice-select">
              <span>Voice</span>
              <select value={voiceId()} onChange={(event) => selected() && props.updateVoice(props.provider.provider, selected()!.model, event.currentTarget.value)}>
                <For each={props.visibleVoices(props.provider)}>{(voice) => <option value={voice.voice_id}>{voice.label}</option>}</For>
              </select>
            </label>
            <label class="openflow-field tts-language-select">
              <span>Language</span>
              <select value={language()} onChange={(event) => props.updateLanguage(keyBase(), event.currentTarget.value)}>
                <For each={XAI_LANGUAGE_OPTIONS}>{(option) => <option value={option.value}>{option.label}</option>}</For>
              </select>
            </label>
            <button class="tts-play-icon-button" type="button" title="Play preview" aria-label="Play preview" disabled={preview()?.status === "generating" || !selected() || !effectiveVoiceId()} onClick={() => void play()}>
              <PlayIcon />
            </button>
          </div>
          <label class="openflow-field tts-full-row">
            <span>Simple Prompt</span>
            <textarea rows={5} value={simplePrompt()} onInput={(event) => setSimplePrompts((prev) => ({ ...prev, [keyBase()]: event.currentTarget.value }))} />
          </label>
          <Show when={scenario().usesCustomVoice}>
            <div class="tts-custom-voice-field">
              <label class="openflow-field">
                <span>Custom Voice ID</span>
                <input value={customVoiceId()} placeholder="Paste xAI custom voice_id, e.g. nlbqfwie" onInput={(event) => setCustomVoiceIds((prev) => ({ ...prev, [keyBase()]: event.currentTarget.value }))} />
              </label>
              <a class="tts-console-link" href={XAI_CUSTOM_VOICE_CONSOLE_URL} target="_blank" rel="noreferrer">
                Create in xAI Console
              </a>
            </div>
          </Show>
          <div class="tts-guide-actions">
            <button class="secondary" type="button" onClick={buildComplexPrompt}>Generate Complex Prompt</button>
            <button type="button" disabled={preview()?.status === "generating" || !selected() || !effectiveVoiceId()} onClick={() => void play()}>
              {preview()?.status === "generating" ? "Generating..." : preview()?.status === "playing" ? "Playing..." : "Play Preview"}
            </button>
          </div>
          <label class="openflow-field tts-full-row">
            <span>Complex Prompt</span>
            <textarea class="tts-complex-prompt" rows={10} placeholder="Click Generate Complex Prompt, then edit before previewing." value={complexPrompt()} onInput={(event) => setComplexPrompts((prev) => ({ ...prev, [keyBase()]: event.currentTarget.value }))} />
          </label>
          <div class="tts-guide-links">
            <a href={props.provider.docs_url} target="_blank" rel="noreferrer">Provider Docs</a>
            <Show when={scenario().usesCustomVoice}>
              <a href={XAI_CUSTOM_VOICE_CONSOLE_URL} target="_blank" rel="noreferrer">xAI Console</a>
            </Show>
          </div>
          <Show when={preview()?.status === "failed"}>
            <p class="tts-preview-error">{preview()?.error}</p>
          </Show>
        </div>
      </div>
      <Show when={infoScenario()}>{(item) => (
        <>
          <div class="drawer-backdrop tts-info-backdrop" onClick={() => setInfoScenarioId("")} />
          <div class="env-dialog tts-info-dialog" onClick={(event) => event.stopPropagation()}>
            <div class="env-dialog-head">
              <h3>{item().infoTitle}</h3>
              <button class="icon-action" type="button" title="Close" onClick={() => setInfoScenarioId("")}><CloseIcon /></button>
            </div>
            <p>{item().infoBodyZh}</p>
            <Show when={item().id === "xai-voices"}>
              <div class="tts-accent-table">
                <For each={XAI_VOICE_GUIDE}>{(voice) => (
                  <div>
                    <strong>{voice.voice}</strong>
                    <span>{voice.tone}</span>
                    <p>{voice.zh}</p>
                  </div>
                )}</For>
              </div>
            </Show>
            <Show when={item().id === "xai-language"}>
              <div class="tts-audio-tag-table">
                <For each={XAI_LANGUAGE_OPTIONS}>{(language) => (
                  <div>
                    <code>{language.value}</code>
                    <span>{language.label}</span>
                  </div>
                )}</For>
              </div>
            </Show>
            <Show when={item().id === "xai-inline-tags"}>
              <div class="tts-audio-tag-table">
                <For each={XAI_INLINE_TAGS}>{(tag) => (
                  <div>
                    <code>{tag.tag}</code>
                    <span>{tag.zh}</span>
                  </div>
                )}</For>
              </div>
            </Show>
            <Show when={item().id === "xai-wrapping-tags"}>
              <div class="tts-audio-tag-table">
                <For each={XAI_WRAPPING_TAGS}>{(tag) => (
                  <div>
                    <code>{tag.tag}</code>
                    <span>{tag.zh}</span>
                  </div>
                )}</For>
              </div>
            </Show>
            <div class="tts-info-tags">
              <For each={item().verifies}>{(verify) => <span>{verify}</span>}</For>
            </div>
          </div>
        </>
      )}</Show>
    </>
  );
}
