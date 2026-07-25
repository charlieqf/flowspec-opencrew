export type GoogleTTSKeywordGroup = {
  title: string;
  words: string[];
};

export type GoogleTTSScenario = {
  id: string;
  label: string;
  category: string;
  defaultVoice: string;
  secondVoice?: string;
  speaker1?: string;
  speaker2?: string;
  multiSpeaker?: boolean;
  defaultLanguage: "zh" | "en";
  simplePrompt: string;
  infoTitle: string;
  infoBodyZh: string;
  verifies: string[];
  groups: GoogleTTSKeywordGroup[];
  buildComplexPrompt: (simplePrompt: string, language: string) => string;
};

export const GOOGLE_TTS_SCENARIOS: GoogleTTSScenario[] = [
  {
    id: "single-basic",
    label: "单说话人基础朗读",
    category: "基础",
    defaultVoice: "Kore",
    defaultLanguage: "zh",
    simplePrompt: "请用自然、清晰、稳定的语气朗读：欢迎使用 OpenCrew。我们正在测试 Gemini TTS 的基础单说话人朗读效果。",
    infoTitle: "单说话人基础朗读",
    infoBodyZh: "验证最基础的 text-to-speech：选择一个预建 voice，将文本精准转换成单人音频。主要听清晰度、音色稳定性和是否只朗读转写内容。",
    verifies: ["单说话人 VoiceConfig", "基础朗读清晰度", "文本到音频输出"],
    groups: [
      { title: "朗读风格", words: ["clear narration", "自然朗读", "稳定音量", "少情绪"] },
      { title: "节奏", words: ["中速", "句尾干净", "轻微停顿", "不拖长"] },
      { title: "边界", words: ["只朗读正文", "不读标题", "不读导演说明"] },
    ],
    buildComplexPrompt: (simple) => `# AUDIO PROFILE: Clear Narrator\n## "Single Speaker Baseline"\n\n### DIRECTOR'S NOTES\nStyle: Natural, clear, steady, and trustworthy.\nPacing: Medium pace with clean sentence endings.\nDelivery: Read only the transcript. Do not read section headings or notes.\n\n### TRANSCRIPT\n${simple}`,
  },
  {
    id: "style-control",
    label: "单说话人风格控制",
    category: "风格",
    defaultVoice: "Puck",
    defaultLanguage: "zh",
    simplePrompt: "请用欢快、有感染力、适合短视频开场的方式介绍：今天我们用 OpenCrew 快速验证一段商业旁白。",
    infoTitle: "单说话人风格控制",
    infoBodyZh: "验证 Google 文档提到的自然语言风格控制能力：用提示词指导风格、语气、节奏和整体表演，而不是只机械朗读文本。",
    verifies: ["style", "tone", "pace", "自然语言控制表演"],
    groups: [
      { title: "风格", words: ["可信赖", "亲切真实", "vocal smile", "专业讲解"] },
      { title: "语气", words: ["温和", "有信心", "不夸张", "像真人口播"] },
      { title: "节奏", words: ["中速", "短停顿", "重音自然"] },
    ],
    buildComplexPrompt: (simple) => `# AUDIO PROFILE: Short-form Host\n## "Bright Commercial Opener"\n\n## THE SCENE\nA compact recording booth for a polished product demo video.\n\n### DIRECTOR'S NOTES\nStyle: Upbeat, confident, friendly, and commercially polished.\nPacing: Energetic but still easy to understand.\nDelivery: Smile in the voice. Emphasize the product name lightly.\n\n### TRANSCRIPT\n${simple}`,
  },
  {
    id: "emotion-tags",
    label: "情绪标签控制",
    category: "音频标签",
    defaultVoice: "Aoede",
    defaultLanguage: "zh",
    simplePrompt: "[excitedly] 大家好，我是一个新的文字转语音模型，可以用多种方式说话。[bored] 当然，我也可以听起来很无聊。[whispers] 现在我把声音放低一点。",
    infoTitle: "情绪标签控制",
    infoBodyZh: "验证方括号音频标签，例如 [excitedly]、[bored]、[whispers]。Google 文档没有把标签限定为固定枚举，而是建议尝试不同情绪和表达；下表是文档中提到的常用和示例标签。非英语文本也建议优先使用英语标签。",
    verifies: ["audio tags", "情绪切换", "标签驱动的表达变化"],
    groups: [
      { title: "情绪标签", words: ["[excitedly]", "[bored]", "[reluctantly]", "[amazed]", "[crying]", "[curious]", "[excited]", "[mischievously]", "[panicked]", "[sarcastic]", "[serious]", "[tired]", "[trembling]"] },
      { title: "速度与强调", words: ["[very fast]", "[very slow]", "[sarcastically, one painfully slow word at a time]"] },
      { title: "局部语气", words: ["[whispers]", "[shouting]"] },
      { title: "非语言声音", words: ["[laughs]", "[sighs]", "[gasp]", "[giggles]", "[cough]"] },
    ],
    buildComplexPrompt: (simple) => `# AUDIO PROFILE: Tag Tester\n## "Inline Emotion Tags"\n\n### DIRECTOR'S NOTES\nStyle: Follow every bracketed audio tag precisely.\nPacing: Let each emotional section feel distinct.\nDelivery: Do not explain the tags. Perform them.\n\n### TRANSCRIPT\n${simple}`,
  },
  {
    id: "local-tone-shift",
    label: "局部语气切换",
    category: "音频标签",
    defaultVoice: "Iapetus",
    defaultLanguage: "zh",
    simplePrompt: "[whispers] 这是一个很小声的开场。 [shouting] 现在把重点打出来！ [whispers] 然后再回到克制、近距离的语气。",
    infoTitle: "局部语气切换",
    infoBodyZh: "验证同一段转写内容里局部语气变化：低语、强调、再回到低语。主要听模型是否能在短文本中切换投射感。",
    verifies: ["局部控制", "whispers", "shouting", "同段落动态变化"],
    groups: [
      { title: "切换词", words: ["先平稳", "转为强调", "收束放慢", "末尾确认"] },
      { title: "语气", words: ["提醒感", "解释感", "确认感", "故事感"] },
      { title: "边界", words: ["只影响当前短句", "不要改变角色整体声音"] },
    ],
    buildComplexPrompt: (simple) => `# AUDIO PROFILE: Dynamic Performer\n## "Whisper to Projection"\n\n### DIRECTOR'S NOTES\nStyle: Highly responsive to inline delivery tags.\nDynamics: Make the whisper intimate and the shouted section projected without distortion.\nPacing: Pause briefly between mode changes.\n\n### TRANSCRIPT\n${simple}`,
  },
  {
    id: "pacing-control",
    label: "速度控制",
    category: "节奏",
    defaultVoice: "Laomedeia",
    defaultLanguage: "zh",
    simplePrompt: "[very fast] 快速介绍三个功能点：配置模型、生成提示词、播放预览。[very slow] 现在慢下来，让每个词都有足够空间。",
    infoTitle: "速度控制",
    infoBodyZh: "验证 pacing 控制。文档既给出 [very fast] / [very slow] 标签，也建议在导演笔记中明确整体节奏。",
    verifies: ["pacing", "very fast", "very slow", "节奏变化"],
    groups: [
      { title: "速度", words: ["略快但清晰", "中速", "慢速强调", "快速开场"] },
      { title: "停顿", words: ["短停顿", "句尾干净", "逗号轻停", "段落停顿"] },
      { title: "风险规避", words: ["不抢话", "不含糊", "不吞字"] },
    ],
    buildComplexPrompt: (simple) => `# AUDIO PROFILE: Tempo Tester\n## "Speed Contrast"\n\n### DIRECTOR'S NOTES\nStyle: Clear and controlled.\nPacing: Obey the speed tags exactly. The fast part should feel energetic; the slow part should feel deliberate and spacious.\nDelivery: Preserve intelligibility even during fast delivery.\n\n### TRANSCRIPT\n${simple}`,
  },
  {
    id: "non-verbal",
    label: "非语言声音",
    category: "音频标签",
    defaultVoice: "Vindemiatrix",
    defaultLanguage: "zh",
    simplePrompt: "[sighs] 好吧，我们再试一次。[gasp] 等等，这次效果好像更自然了。[laughs] 这就是我们想验证的非语言表达。",
    infoTitle: "非语言声音",
    infoBodyZh: "验证 [sighs]、[gasp]、[laughs] 等非语言声音。主要观察插入效果是否自然，以及是否破坏正文可懂度。",
    verifies: ["非语言音频标签", "插入式表达", "自然度"],
    groups: [
      { title: "声音标签", words: ["[laughs]", "[sighs]", "[gasp]", "[yawn]"] },
      { title: "使用位置", words: ["开头轻笑", "句中低声", "结尾呼吸"] },
      { title: "限制", words: ["少量使用", "不打断内容", "不替代台词"] },
    ],
    buildComplexPrompt: (simple) => `# AUDIO PROFILE: Expressive Narrator\n## "Non-verbal Markers"\n\n### DIRECTOR'S NOTES\nStyle: Conversational and expressive without becoming theatrical.\nDelivery: Perform bracketed non-verbal sounds naturally, then return to clear speech.\n\n### TRANSCRIPT\n${simple}`,
  },
  {
    id: "multi-dialogue",
    label: "多说话人对话",
    category: "多说话人",
    defaultVoice: "Kore",
    secondVoice: "Puck",
    speaker1: "小林",
    speaker2: "小周",
    multiSpeaker: true,
    defaultLanguage: "zh",
    simplePrompt: "小林: 今天这个 TTS 配置测试得怎么样？\n小周: 还不错，我们可以听出两个说话人的声音是否清楚区分。",
    infoTitle: "多说话人对话",
    infoBodyZh: "验证 Google TTS 的 MultiSpeakerVoiceConfig。最多 2 个 speaker，并且提示词中的 speaker 名称要和配置里的名称一致。",
    verifies: ["MultiSpeakerVoiceConfig", "两位说话人", "speaker 名称匹配"],
    groups: [
      { title: "对话关系", words: ["自然接话", "互相回应", "角色区分明显", "不抢话"] },
      { title: "轮次", words: ["短句轮换", "turns distinct", "不要读 speaker label"] },
      { title: "节奏", words: ["轻快", "有互动感", "停顿明确"] },
    ],
    buildComplexPrompt: (simple) => `# MULTI-SPEAKER TTS SCENE\n## "Two Speaker Baseline"\n\n### DIRECTOR'S NOTES\n小林: 语气温和、自然，像在认真确认测试结果。\n小周: 语气轻快、放松，回应要清楚直接。\nKeep speaker turns distinct and do not read speaker labels as narration.\n\n### TRANSCRIPT\n${simple}`,
  },
  {
    id: "multi-character-contrast",
    label: "多说话人角色差异",
    category: "多说话人",
    defaultVoice: "Enceladus",
    secondVoice: "Puck",
    speaker1: "甲",
    speaker2: "乙",
    multiSpeaker: true,
    defaultLanguage: "zh",
    simplePrompt: "甲: [yawn] 今天还要继续测试哪些功能？\n乙: [excitedly] 你一定想不到，这次我们要听出疲惫和兴奋的明显差异！",
    infoTitle: "多说话人角色差异",
    infoBodyZh: "验证每个 speaker 的单独表演指导。文档举例说明 Enceladus 的气声适合疲惫无聊，Puck 的欢快适合兴奋开心。",
    verifies: ["speaker-specific guidance", "voice 与情绪匹配", "角色差异"],
    groups: [
      { title: "差异", words: ["沉稳 vs 活泼", "讲解者 vs 提问者", "成熟 vs 年轻"] },
      { title: "声音", words: ["Kore 可靠", "Puck 活跃", "Aoede 明亮"] },
      { title: "一致性", words: ["同一角色保持一致", "不要互相串音"] },
    ],
    buildComplexPrompt: (simple) => `# MULTI-SPEAKER TTS SCENE\n## "Tired vs Excited"\n\n### DIRECTOR'S NOTES\n甲: 疲惫、无聊、带一点气声，能量低，语速偏慢。\n乙: 兴奋、明亮、开心，回应速度快。\nThe contrast between the two voices is the main test.\n\n### TRANSCRIPT\n${simple}`,
  },
  {
    id: "podcast-transcript",
    label: "播客脚本",
    category: "Cookbook",
    defaultVoice: "Kore",
    secondVoice: "Puck",
    speaker1: "安雅博士",
    speaker2: "李昂",
    multiSpeaker: true,
    defaultLanguage: "zh",
    simplePrompt: "安雅博士: 这种小型沙漠壁虎会把夜晚变成自己的捕食优势。\n李昂: 听起来像一个超级英雄起源故事，只不过它的超能力是更好的伪装。",
    infoTitle: "播客脚本",
    infoBodyZh: "对应 Cookbook 里“先用 Gemini 生成播客转写稿，再交给 TTS 朗读”的流程。这里先让你直接编辑转写稿并试听播客感。",
    verifies: ["播客式转写稿", "双主持人", "Cookbook 工作流"],
    groups: [
      { title: "风格", words: ["自然接话", "轻松", "有互动感", "像真实播客"] },
      { title: "节奏", words: ["中速", "轻微停顿", "不要太正式"] },
      { title: "角色", words: ["主持人", "嘉宾", "解释者", "追问者"] },
    ],
    buildComplexPrompt: (simple) => `# MULTI-SPEAKER TTS SCENE\n## "Excited Science Podcast"\n\n## THE SCENE\n一个明亮、轻松的科普播客录音间，两位主持人正在用中文讨论有趣的动物知识。\n\n### DIRECTOR'S NOTES\n安雅博士: 专业、准确，对细节有兴奋感。\n李昂: 好奇、机智、有活力，像轻松接话的联合主持人。\nPacing: Natural podcast rhythm with quick but clear turns.\n\n### TRANSCRIPT\n${simple}`,
  },
  {
    id: "audiobook",
    label: "有声书旁白",
    category: "高级提示",
    defaultVoice: "Achernar",
    defaultLanguage: "zh",
    simplePrompt: "夜色落在玻璃窗上，城市的灯像一条缓慢流动的河。她关掉屏幕，终于听见了房间里自己的呼吸。",
    infoTitle: "有声书旁白",
    infoBodyZh: "验证 TTS 用于有声书或长叙事时的稳定性、画面感和情绪克制。也验证高级提示中的 Scene 与 Director's Notes。",
    verifies: ["叙事朗读", "场景氛围", "长文本稳定性"],
    groups: [
      { title: "旁白质感", words: ["画面感", "沉浸", "克制", "有故事感"] },
      { title: "节奏", words: ["慢速", "段落停顿", "句尾收住", "不急促"] },
      { title: "情绪", words: ["温柔", "悬念", "安静", "轻微起伏"] },
    ],
    buildComplexPrompt: (simple) => `# AUDIO PROFILE: Literary Narrator\n## "Quiet Night Narration"\n\n## THE SCENE\nA calm late-night audiobook recording. The room is quiet and close, with no urgency.\n\n### DIRECTOR'S NOTES\nStyle: Soft, cinematic, reflective.\nPacing: Slow and liquid, but not sleepy.\nDelivery: Let imagery breathe. Keep consonants clear.\n\n### TRANSCRIPT\n${simple}`,
  },
  {
    id: "commercial-short",
    label: "广告 / 短视频旁白",
    category: "业务",
    defaultVoice: "Sadachbia",
    defaultLanguage: "zh",
    simplePrompt: "三秒钟，让客户知道你是谁；十秒钟，让客户记住你的价值。OpenCrew 帮你把复杂流程，变成可复用的智能体系统。",
    infoTitle: "广告 / 短视频旁白",
    infoBodyZh: "验证商业短视频常见需求：节奏、重点词强调、清晰口播、可信但不夸张的销售感。",
    verifies: ["商业口播", "重点词强调", "短视频节奏"],
    groups: [
      { title: "风格", words: ["vocal smile", "自然口播", "商业短视频", "亲切真实"] },
      { title: "节奏", words: ["略快但清晰", "短停顿", "句尾干净", "不抢话"] },
      { title: "音频标签", words: ["[excitedly]", "[laughs]", "[brightly]"] },
    ],
    buildComplexPrompt: (simple) => `# AUDIO PROFILE: Commercial Narrator\n## "Concise Product Pitch"\n\n### DIRECTOR'S NOTES\nStyle: Confident, bright, persuasive, but not exaggerated.\nPacing: Fast enough for short-form video, with crisp pauses after key value statements.\nDelivery: Emphasize OpenCrew and value phrases lightly.\n\n### TRANSCRIPT\n${simple}`,
  },
  {
    id: "accent-control",
    label: "口音控制",
    category: "高级提示",
    defaultVoice: "Aoede",
    defaultLanguage: "en",
    simplePrompt: "Welcome back to the studio. Today we are testing whether a voice can carry a specific regional flavor while staying easy to understand.",
    infoTitle: "口音控制",
    infoBodyZh: "验证 Accent 指令。Google 文档没有提供固定支持口音枚举，也没有确认中文地域口音可稳定支持；文档建议用具体地区和人物背景描述口音。下表列出文档明确提到的可测试口音示例。",
    verifies: ["Accent", "具体口音描述", "可懂度"],
    groups: [
      { title: "口音", words: ["Mandarin", "轻微地域感", "标准普通话", "自然口音"] },
      { title: "清晰度", words: ["发音清楚", "不影响理解", "不要夸张模仿"] },
      { title: "语气", words: ["亲切", "自然", "可信"] },
    ],
    buildComplexPrompt: (simple) => `# AUDIO PROFILE: Regional Presenter\n## "Regional Accent Test"\n\n## THE SCENE\nA bright radio studio with a confident presenter speaking directly to listeners.\n\n### DIRECTOR'S NOTES\nStyle: Charismatic radio presenter.\nAccent: Southern California valley girl from Laguna Beach. Distinct regional flavor, but still clear to an international audience.\nPacing: Energetic and smooth.\n\n### TRANSCRIPT\n${simple}`,
  },
  {
    id: "advanced-director",
    label: "高级导演提示",
    category: "高级提示",
    defaultVoice: "Laomedeia",
    defaultLanguage: "zh",
    simplePrompt: "[excitedly] 录音间的气氛已经起来了！你正在收听一段高能开场，我们马上进入今天最重要的功能测试。",
    infoTitle: "高级导演提示",
    infoBodyZh: "验证文档中的 Markdown 高级提示结构：Audio Profile、Scene、Director's Notes、Sample Context、Transcript。适合复杂表演方向。",
    verifies: ["Audio Profile", "Scene", "Director's Notes", "Transcript"],
    groups: [
      { title: "导演笔记", words: ["THE SCENE", "DIRECTOR'S NOTES", "TRANSCRIPT"] },
      { title: "限制", words: ["Do not read speaker labels", "Keep turns distinct", "只朗读正文"] },
      { title: "质量", words: ["近距离收音", "节奏紧凑", "自然清晰"] },
    ],
    buildComplexPrompt: (simple) => `# AUDIO PROFILE: 晨间节目主持人\n## "The Morning Hype"\n\n## THE SCENE: 城市录音间\n夜晚的城市灯光映在玻璃墙上，但录音间里灯光明亮，红色 ON AIR 指示灯已经亮起。主持人站在调音台前，准备把听众的注意力拉起来。\n\n### DIRECTOR'S NOTES\nStyle:\n- The Vocal Smile: 声音里要能听见笑意，明亮、积极、有邀请感。\n- Dynamics: 投射感强，但不要喊叫。\nPacing: 有能量、有弹性，句子之间不要拖沓。\nAccent: 清晰的中文普通话，带一点年轻节目主持人的松弛感。\n\n### SAMPLE CONTEXT\n适合活动预热、产品发布、短视频开场和高能播客开场。\n\n### TRANSCRIPT\n${simple}`,
  },
  {
    id: "classifier-safe",
    label: "提示分类器规避",
    category: "安全",
    defaultVoice: "Iapetus",
    defaultLanguage: "zh",
    simplePrompt: "以下是需要合成语音的转写内容，请只朗读转写内容：OpenCrew 正在测试清晰的提示结构，避免把导演说明读出来。",
    infoTitle: "提示分类器规避",
    infoBodyZh: "验证文档限制中提到的问题：模糊提示可能导致模型朗读导演说明或被拒绝。这个情景用明确前言和 TRANSCRIPT 标记降低风险。",
    verifies: ["清晰转写边界", "避免读出导演说明", "降低提示误分类"],
    groups: [
      { title: "边界", words: ["清晰转写边界", "只朗读 TRANSCRIPT", "不读 headings"] },
      { title: "规避", words: ["避免提示误分类", "不要朗读导演说明", "明确合成语音请求"] },
      { title: "风格", words: ["中性", "清楚", "精确"] },
    ],
    buildComplexPrompt: (simple) => `# AUDIO SYNTHESIS REQUEST\nGenerate speech only for the text under TRANSCRIPT. Do not read headings, notes, or instructions aloud.\n\n### DIRECTOR'S NOTES\nStyle: Clear, neutral, precise.\nPacing: Medium.\n\n### TRANSCRIPT\n${simple}`,
  },
];

export const GOOGLE_TTS_SCENARIO_GUIDES = GOOGLE_TTS_SCENARIOS;

export function googleTtsScenarioById(id: string) {
  return GOOGLE_TTS_SCENARIOS.find((item) => item.id === id) || GOOGLE_TTS_SCENARIOS[0];
}

export function googleTtsScenarioWords(id: string) {
  return googleTtsScenarioById(id).groups.flatMap((group) => group.words);
}
