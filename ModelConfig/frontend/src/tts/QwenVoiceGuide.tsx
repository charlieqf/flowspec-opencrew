import { For, Show, createEffect, createMemo, createSignal } from "solid-js";
import { CloseIcon, PlayIcon } from "../shared/icons";
import type { MediaModelConfigResponse, TTSPreviewState } from "../shared/types";

type Provider = MediaModelConfigResponse["providers"][number];
type Voice = NonNullable<Provider["models"][number]["voices"]>[number];

type PreviewOptions = {
  sampleText?: string;
  complexPrompt?: string;
  voiceId?: string;
  language?: string;
};

type QwenScenario = {
  id: string;
  label: string;
  modelType: "all" | "flash" | "instruct";
  defaultVoice: string;
  defaultLanguage: string;
  simplePrompt: string;
  infoTitle: string;
  infoBodyZh: string;
  verifies: string[];
  buildComplexPrompt: (simplePrompt: string, voiceLabel: string, language: string) => string;
};

const QWEN_DOC_URL = "https://help.aliyun.com/zh/model-studio/qwen-tts";
const QWEN_CONSOLE_DOC_URL = "https://bailian.console.aliyun.com/cn-beijing/?tab=doc#/doc/?type=model&url=2938790";
const QWEN_API_DOC_URL = "https://bailian.console.aliyun.com/cn-beijing/?tab=doc#/doc/?type=model&url=2879134";

const QWEN_LANGUAGE_OPTIONS = [
  { value: "Chinese", label: "中文 / 方言音色" },
  { value: "English", label: "English" },
  { value: "French", label: "Français" },
  { value: "German", label: "Deutsch" },
  { value: "Russian", label: "Русский" },
  { value: "Italian", label: "Italiano" },
  { value: "Spanish", label: "Español" },
  { value: "Portuguese", label: "Português" },
  { value: "Japanese", label: "日本語" },
  { value: "Korean", label: "한국어" },
];

const QWEN_SCENARIOS: QwenScenario[] = [
  {
    id: "qwen-basic",
    label: "基础中文朗读",
    modelType: "all",
    defaultVoice: "Cherry",
    defaultLanguage: "Chinese",
    simplePrompt: "欢迎使用 OpenCrew。我们正在测试千问 TTS 的中文普通话朗读、清晰度、自然度和节奏稳定性。",
    infoTitle: "基础中文朗读",
    infoBodyZh: "验证系统音色的基础合成能力：text、voice、language_type。百炼文档建议 language_type 与文本语种一致，以获得正确发音和自然语调。",
    verifies: ["系统音色", "language_type", "普通话自然度"],
    buildComplexPrompt: (simple) => simple,
  },
  {
    id: "qwen-voice-match",
    label: "音色适配对比",
    modelType: "all",
    defaultVoice: "Serena",
    defaultLanguage: "Chinese",
    simplePrompt: "这是一段品牌介绍旁白。语气要清楚、稳定、可信，同时保留一点亲和力，适合放在产品演示视频里。",
    infoTitle: "音色适配对比",
    infoBodyZh: "验证当前模型可选的所有 voice 是否能正常合成，并通过中文描述帮助判断音色适合商务、教育、角色、广告还是生活化内容。",
    verifies: ["模型音色列表", "音色描述", "场景匹配"],
    buildComplexPrompt: (simple, voiceLabel) => `# Qwen voice match\nVoice: ${voiceLabel}\n\n${simple}`,
  },
  {
    id: "qwen-multilingual",
    label: "多语言/中英混读",
    modelType: "all",
    defaultVoice: "Ethan",
    defaultLanguage: "English",
    simplePrompt: "Hello, welcome to OpenCrew. 今天我们测试同一个音色在 English 和中文之间切换时，发音是否自然、节奏是否稳定。",
    infoTitle: "多语言/中英混读",
    infoBodyZh: "验证 Qwen TTS 多语种能力。系统音色支持中文普通话、英语、法语、德语、俄语、意大利语、西班牙语、葡萄牙语、日语、韩语等，部分方言音色还支持中文方言。",
    verifies: ["多语种", "中英混读", "发音稳定"],
    buildComplexPrompt: (simple, voiceLabel, language) => `# Qwen multilingual test\nVoice: ${voiceLabel}\nLanguage type: ${language}\n\n${simple}`,
  },
  {
    id: "qwen-dialect",
    label: "中文方言音色",
    modelType: "flash",
    defaultVoice: "Sunny",
    defaultLanguage: "Chinese",
    simplePrompt: "今天天气巴适得很，我们来试一下地方口音的自然度、亲切感和短视频旁白的生活气。",
    infoTitle: "中文方言音色",
    infoBodyZh: "验证 Flash 系列里的方言音色，例如上海话、北京话、南京话、陕西话、闽南语、天津话、四川话、粤语。注意：百炼接口的 language_type 不接受 Sichuanese/Cantonese 等方言值，方言通过 voice 音色体现，language_type 仍传 Chinese。",
    verifies: ["Flash-only 音色", "中文方言", "language_type=Chinese", "地域化表达"],
    buildComplexPrompt: (simple, voiceLabel, language) => `# Qwen dialect test\nVoice: ${voiceLabel}\nLanguage type: ${language}\n\n${simple}`,
  },
  {
    id: "qwen-news",
    label: "新闻/知识讲解",
    modelType: "all",
    defaultVoice: "Neil",
    defaultLanguage: "Chinese",
    simplePrompt: "下面进入今日重点。人工智能正在改变内容生产流程，企业需要同时关注效率、质量和合规边界。",
    infoTitle: "新闻/知识讲解",
    infoBodyZh: "验证清晰咬字、稳定基线语调和信息密度较高文本的可懂度。适合测试 Neil、Elias、Maia 等偏播报或讲解的音色。",
    verifies: ["吐字清晰", "信息密度", "讲解场景"],
    buildComplexPrompt: (simple) => simple,
  },
  {
    id: "qwen-instruct-pacing",
    label: "指令控制：语速语调",
    modelType: "instruct",
    defaultVoice: "Cherry",
    defaultLanguage: "Chinese",
    simplePrompt: "这款产品今天正式上线。前三秒要迅速抓住注意力，中段讲清核心卖点，结尾要有明确行动感。",
    infoTitle: "指令控制：语速语调",
    infoBodyZh: "仅用于支持指令控制的模型系列。验证 instructions 参数对语速、音调、音量和用途风格的控制效果；描述文本仅支持中文和英文，长度不得超过 1600 Token。",
    verifies: ["instructions", "语速", "语调", "用途风格"],
    buildComplexPrompt: (simple, voiceLabel) => `语速偏快，音调略高，前三秒有明显上扬语调，整体充满活力和感染力，适合广告配音。使用 ${voiceLabel} 保持清晰自然。\n\n朗读文本：${simple}`,
  },
  {
    id: "qwen-instruct-emotion",
    label: "指令控制：情绪递进",
    modelType: "instruct",
    defaultVoice: "Bellona",
    defaultLanguage: "Chinese",
    simplePrompt: "我一开始只是有点惊讶，后来越来越激动，直到最后忍不住大声说：这就是我们一直在等的答案。",
    infoTitle: "指令控制：情绪递进",
    infoBodyZh: "验证 Instruct 对情绪变化和音量递进的控制。文档示例包括“音量由正常对话迅速增强至高喊”“情绪易激动且外露”。",
    verifies: ["情绪", "音量变化", "戏剧张力"],
    buildComplexPrompt: (simple) => `情绪从克制惊讶逐步增强到激动，音量由正常对话逐渐提高，最后一句有明显爆发力；吐字仍需清晰，不要失真。\n\n朗读文本：${simple}`,
  },
  {
    id: "qwen-instruct-audiobook",
    label: "指令控制：有声书角色",
    modelType: "instruct",
    defaultVoice: "Eldric Sage",
    defaultLanguage: "Chinese",
    simplePrompt: "夜色压下来时，老人终于开口了。他说，真正重要的东西，从来不会在喧哗里出现。",
    infoTitle: "指令控制：有声书角色",
    infoBodyZh: "验证有声书、广播剧、游戏角色和动画配音场景。重点听角色性格、低沉感、停顿和叙事氛围是否符合指令。",
    verifies: ["有声书", "角色性格", "叙事氛围"],
    buildComplexPrompt: (simple) => `低沉、缓慢、沉稳，带有年长叙述者的沧桑感；停顿略长，像在讲一个重要的秘密；整体适合有声书旁白。\n\n朗读文本：${simple}`,
  },
  {
    id: "qwen-instruct-dongbei",
    label: "指令尝试：东北口音",
    modelType: "instruct",
    defaultVoice: "Ethan",
    defaultLanguage: "Chinese",
    simplePrompt: "今儿咱们来试试这个功能好不好使。说话得自然点，别太端着，就像跟朋友唠嗑一样，把重点说明白。",
    infoTitle: "指令尝试：东北口音",
    infoBodyZh: "官方系统音色没有明确列出东北话 voice_id。这个情景只通过 Instruct 的 instructions 尝试让普通话带东北口音和聊天感，用来验证模型是否能稳定模拟地方口音。",
    verifies: ["非官方方言", "instructions", "东北口音尝试", "试听验证"],
    buildComplexPrompt: (simple) => `请用自然的中文普通话朗读，但带明显东北口音和东北人日常聊天的语气；语速中等偏快，语调爽朗、热情、接地气，像在跟熟人唠嗑。保留文本原意，不要额外改写内容，不要夸张到听不清。\n\n朗读文本：${simple}`,
  },
  {
    id: "qwen-instruct-hubei",
    label: "指令尝试：湖北口音",
    modelType: "instruct",
    defaultVoice: "Maia",
    defaultLanguage: "Chinese",
    simplePrompt: "今天我们来测试一段地方口音。语气要亲切一点，像在武汉街头跟朋友解释事情，重点清楚，节奏自然。",
    infoTitle: "指令尝试：湖北口音",
    infoBodyZh: "官方系统音色没有明确列出湖北话或武汉话 voice_id。这个情景只通过 Instruct 的 instructions 尝试让普通话带湖北/武汉口音，用来验证是否能通过提示词获得可接受的地方口音。",
    verifies: ["非官方方言", "instructions", "湖北口音尝试", "试听验证"],
    buildComplexPrompt: (simple) => `请用自然的中文普通话朗读，但带一点湖北武汉口音和本地聊天语气；语调亲切、直接、生活化，节奏不要太慢，像在街头自然解释事情。保留文本原意，不要额外改写内容，口音要明显但仍然清晰易懂。\n\n朗读文本：${simple}`,
  },
];

const modelSupportsInstructions = (model: Provider["models"][number]) => (
  Boolean(model.supports_prompt_builder)
  || model.voice_modes?.includes("instruct_prompt")
  || Boolean(model.voices?.some((voice) => voice.mode === "instruct_prompt"))
);

const scenarioFitsModel = (scenario: QwenScenario, model: Provider["models"][number]) => {
  const supportsInstructions = modelSupportsInstructions(model);
  if (scenario.modelType === "instruct") return supportsInstructions;
  if (scenario.modelType === "flash") return !supportsInstructions;
  return true;
};

const scenarioVoice = (scenario: QwenScenario, voices: Voice[]) => (
  voices.find((voice) => voice.voice_id === scenario.defaultVoice)?.voice_id || voices[0]?.voice_id || ""
);

const voiceSummary = (voice: Voice) => `${voice.label}${voice.style ? `；${voice.style}` : ""}`;

export function QwenVoiceGuide(props: {
  provider: Provider;
  onClose: () => void;
  selectedModel: (provider: Provider) => Provider["models"][number] | null;
  selectedVoiceId: (provider: Provider) => string;
  visibleVoices: (provider: Provider) => Voice[];
  modelKey: (provider: string, model: string) => string;
  previewKey: (provider: string, model: string, voiceId: string) => string;
  updateVoice: (provider: string, model: string, voiceId: string) => void;
  voicePreviews: () => Record<string, TTSPreviewState>;
  playPreview: (provider: Provider, override?: PreviewOptions) => Promise<void>;
  selectedLanguage: (key: string) => string;
  updateLanguage: (key: string, language: string) => void;
}) {
  const [scenarioId, setScenarioId] = createSignal("");
  const [simplePrompts, setSimplePrompts] = createSignal<Record<string, string>>({});
  const [complexPrompts, setComplexPrompts] = createSignal<Record<string, string>>({});
  const [infoScenarioId, setInfoScenarioId] = createSignal("");

  const selected = () => props.selectedModel(props.provider);
  const keyBase = () => selected() ? props.modelKey(props.provider.provider, selected()!.model) : "";
  const voices = () => props.visibleVoices(props.provider);
  const scenarios = createMemo(() => QWEN_SCENARIOS.filter((item) => selected() && scenarioFitsModel(item, selected()!)));
  const scenario = () => scenarios().find((item) => item.id === scenarioId()) || scenarios()[0] || QWEN_SCENARIOS[0];
  const infoScenario = () => QWEN_SCENARIOS.find((item) => item.id === infoScenarioId()) ?? null;
  const voiceId = () => props.selectedVoiceId(props.provider);
  const selectedVoice = () => voices().find((voice) => voice.voice_id === voiceId()) || voices()[0];
  const language = () => {
    const current = props.selectedLanguage(keyBase());
    return QWEN_LANGUAGE_OPTIONS.some((option) => option.value === current) ? current : scenario().defaultLanguage;
  };
  const preview = () => selected() ? props.voicePreviews()[props.previewKey(props.provider.provider, selected()!.model, voiceId())] : undefined;
  const simplePrompt = () => simplePrompts()[keyBase()] || scenario().simplePrompt;
  const complexPrompt = () => complexPrompts()[keyBase()] || "";
  const previewText = () => simplePrompt();

  const applyScenario = (nextId: string) => {
    const next = QWEN_SCENARIOS.find((item) => item.id === nextId) || scenarios()[0] || QWEN_SCENARIOS[0];
    const model = selected();
    if (!model) return;
    const key = props.modelKey(props.provider.provider, model.model);
    const nextVoice = scenarioVoice(next, voices());
    setScenarioId(next.id);
    props.updateLanguage(key, next.defaultLanguage);
    setSimplePrompts((prev) => ({ ...prev, [key]: next.simplePrompt }));
    setComplexPrompts((prev) => ({ ...prev, [key]: "" }));
    if (nextVoice) props.updateVoice(props.provider.provider, model.model, nextVoice);
  };

  createEffect(() => {
    const model = selected();
    if (!model || !scenarios().length) return;
    if (!scenarios().some((item) => item.id === scenarioId())) applyScenario(scenarios()[0].id);
  });

  const buildComplexPrompt = () => {
    const voiceLabel = selectedVoice() ? voiceSummary(selectedVoice()!) : voiceId();
    setComplexPrompts((prev) => ({
      ...prev,
      [keyBase()]: scenario().buildComplexPrompt(simplePrompt(), voiceLabel, language()),
    }));
  };

  const play = () => props.playPreview(props.provider, {
    voiceId: voiceId(),
    sampleText: previewText(),
    complexPrompt: complexPrompt(),
    language: language(),
  });

  return (
    <>
      <div class="drawer-backdrop tts-guide-backdrop" onClick={props.onClose} />
      <div class="env-dialog tts-guide-dialog" onClick={(event) => event.stopPropagation()}>
        <div class="env-dialog-head">
          <h3>Qwen Voice Guide</h3>
          <button class="icon-action" type="button" title="Close" onClick={props.onClose}><CloseIcon /></button>
        </div>
        <div class="tts-guide-tool">
          <div class="tts-scenario-row">
            <label class="openflow-field">
              <span>Scenario</span>
              <select value={scenario().id} onChange={(event) => applyScenario(event.currentTarget.value)}>
                <For each={scenarios()}>{(item) => <option value={item.id}>{item.label}</option>}</For>
              </select>
            </label>
            <button class="tts-info-button" type="button" title="Scenario information" aria-label="Scenario information" onClick={() => setInfoScenarioId(scenario().id)}>i</button>
          </div>
          <div class="tts-guide-controls">
            <label class="openflow-field tts-voice-select">
              <span>Voice</span>
              <select value={voiceId()} onChange={(event) => selected() && props.updateVoice(props.provider.provider, selected()!.model, event.currentTarget.value)}>
                <For each={voices()}>{(voice) => <option value={voice.voice_id}>{voice.label}</option>}</For>
              </select>
            </label>
            <label class="openflow-field tts-language-select">
              <span>Language</span>
              <select value={language()} onChange={(event) => props.updateLanguage(keyBase(), event.currentTarget.value)}>
                <For each={QWEN_LANGUAGE_OPTIONS}>{(option) => <option value={option.value}>{option.label}</option>}</For>
              </select>
            </label>
            <button class="tts-play-icon-button" type="button" title="Play preview" aria-label="Play preview" disabled={preview()?.status === "generating" || !selected() || !voiceId()} onClick={() => void play()}>
              <PlayIcon />
            </button>
          </div>
          <label class="openflow-field tts-full-row">
            <span>Simple Prompt</span>
            <textarea rows={5} value={simplePrompt()} onInput={(event) => setSimplePrompts((prev) => ({ ...prev, [keyBase()]: event.currentTarget.value }))} />
          </label>
          <div class="tts-guide-actions">
            <button class="secondary" type="button" onClick={buildComplexPrompt}>Generate Complex Prompt</button>
            <button type="button" disabled={preview()?.status === "generating" || !selected() || !voiceId()} onClick={() => void play()}>
              {preview()?.status === "generating" ? "Generating..." : preview()?.status === "playing" ? "Playing..." : "Play Preview"}
            </button>
          </div>
          <label class="openflow-field tts-full-row">
            <span>Complex Prompt / Instructions</span>
            <textarea class="tts-complex-prompt" rows={10} placeholder="Click Generate Complex Prompt, then edit before previewing. Flash models ignore instructions; Instruct models send this as instructions." value={complexPrompt()} onInput={(event) => setComplexPrompts((prev) => ({ ...prev, [keyBase()]: event.currentTarget.value }))} />
          </label>
          <div class="tts-guide-links">
            <a href={QWEN_DOC_URL} target="_blank" rel="noreferrer">Provider Docs</a>
            <a href={QWEN_CONSOLE_DOC_URL} target="_blank" rel="noreferrer">Model Doc</a>
            <a href={QWEN_API_DOC_URL} target="_blank" rel="noreferrer">API Doc</a>
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
            <Show when={item().id === "qwen-voice-match"}>
              <div class="tts-accent-table">
                <For each={voices()}>{(voice) => (
                  <div>
                    <strong>{voice.voice_id}</strong>
                    <span>{voice.label}</span>
                    <p>{voice.style || "当前模型可选系统音色"}</p>
                  </div>
                )}</For>
              </div>
            </Show>
            <Show when={item().id === "qwen-multilingual" || item().id === "qwen-dialect"}>
              <div class="tts-audio-tag-table">
                <For each={QWEN_LANGUAGE_OPTIONS}>{(language) => (
                  <div>
                    <code>{language.value}</code>
                    <span>{language.label}</span>
                  </div>
                )}</For>
              </div>
            </Show>
            <Show when={item().modelType === "instruct"}>
              <div class="tts-audio-tag-table">
                <div><code>音调</code><span>高音、中音、低音、偏高、偏低</span></div>
                <div><code>语速</code><span>快速、中速、缓慢、偏快、偏慢</span></div>
                <div><code>情感</code><span>开朗、沉稳、温柔、严肃、活泼、冷静、治愈</span></div>
                <div><code>特点</code><span>有磁性、清脆、沙哑、圆润、甜美、浑厚、有力</span></div>
                <div><code>用途</code><span>新闻播报、广告配音、有声书、动画角色、语音助手、纪录片解说</span></div>
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
