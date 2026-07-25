# Koubo Asset Library Voice Agent 设计文档

## 1. 目标

在 Asset Library 左侧导航红框位置新增一个独立的 `Voice Agent`，中文名建议为“语音智能体”。

这个 Agent 面向 Gemini TTS 和 ElevenLabs Text to Dialogue，解决三件事：

1. 用户只给“目的”和少量约束时，自动生成多角色、多轮对话脚本。
2. 自动把脚本转成可控的 TTS 提示词包，包含角色、音色、情绪、节奏、停顿和导出参数。
3. 一键调用 TTS provider 生成音频，并把最终送给 provider 的 payload、提示词版本和生成结果完整落盘。

它不是数字人 Agent 的附属功能。Voice Agent 先生成“声音资产”，后续可被数字人、视频生成、口型驱动或 StoryBoard 对话使用。

## 2. 当前系统基础

| 能力 | 当前落点 | 复用方式 |
| --- | --- | --- |
| Asset Library 左侧导航 | `OpenCrew/frontend/src/modules/koubo/UploadAssetLibrary/components/LibrarySidebar.jsx` | 新增 `voice-agent` 入口，放在“数字人智能体”下方 |
| Asset Library 页面装配 | `OpenCrew/frontend/src/modules/koubo/UploadAssetLibrary/UploadAssetLibraryOverlay.jsx` | 新增 `voice-agent` view |
| 现有 Agent 对话 | `OpenCrew/frontend/src/modules/koubo/UploadAssetLibrary/components/AgentPanel.jsx` / `VideoAgentPanel.jsx` | 复用对话布局、消息卡片、停止、历史回填风格 |
| Prompt Builder 审计 | `SessionContext/PromptBuilder/` | Voice Agent 使用独立 `SessionContext/VoiceAgent/`，避免混入图像/视频 Prompt |
| TTS 模型配置 | `OpenCrew/ModelConfig/backend/opcrew_model_config/media_model_config.py` | Gemini 已在 `tts` 分类里；ElevenLabs 需要新增 provider |
| Gemini TTS 调用 | `OpenCrew/backend/opcrew_backend/koubo/router.py` 中 `generate_google_tts_audio()` | P0 可扩展为 provider service |
| Analysis V1 TTS Builder | `OpenCrew/ToolLibrary/Analysis_V1/03_01_TTSBuilderG.py` 等 | 复用 voice catalog、候选声音、节奏/情绪经验 |

## 3. 产品形态

### 3.1 左侧入口

建议排序：

```text
图像生成
图像智能体
视频生成
视频智能体
数字人智能体
语音智能体
History
```

图标建议使用现有 Material Symbols 风格：

```text
record_voice_over
```

如果当前 icon wrapper 没有这个符号，首版可用 `mic` 或 `graphic_eq`。

### 3.2 主区域

Voice Agent 打开后，整体布局必须和现有 Asset Library 其它入口一致：

```text
左侧导航 | 中间 Voice Asset Library | 右侧 Workspace/Agent 对话
```

中间不是大编辑器，也不是提示词工作台，而是“语音资产库”。用户在这里查看、试听、筛选和管理已经生成或导入的 Voice/Audio 资产；所有创建、改写、情绪控制、角色拆分和生成动作都从右侧对话发起。

| 区域 | 内容 |
| --- | --- |
| 左侧导航 | 图像生成、图像智能体、视频生成、视频智能体、数字人智能体、语音智能体、History |
| 中间顶部工具栏 | 搜索 Voice/Audio、provider 筛选、角色筛选、语言筛选、播放视图切换 |
| 中间 Voice 区域 | Voice/Audio asset 卡片网格或列表；每个卡片可播放、查看脚本、查看 provider、查看 voice、查看生成来源 |
| 中间 Images/Reference 辅助区 | 可选展示用于声音生成的参考文本、参考音频、角色素材或已绑定视频/数字人来源 |
| 右侧 Workspace | Voice Agent 对话、自动出提示词、脚本草稿、provider package 预览、生成确认、生成进度 |
| 右侧 Composer | 输入目的、上传参考音频/文本、选择模板、发送 |

首版不做中间区大编辑器。优先保证：右侧输入目的 -> Agent 生成脚本和提示词 -> 用户确认 -> 生成音频 -> 中间 Voice Asset Library 出现可试听的音频卡片。

### 3.3 中间 Voice Asset Library

中间区域的主对象是声音资产，而不是 Prompt。

每个 Voice/Audio 卡片建议展示：

| 字段 | 说明 |
| --- | --- |
| 播放按钮 | 直接试听生成结果 |
| 标题 | 用户目的摘要或生成名，例如 `双人播客开场` |
| Provider / Model | Gemini 或 ElevenLabs，以及具体模型 |
| Voices / Speakers | 角色数量、角色名、voice id/name |
| Duration | 音频时长 |
| Emotion | 主要情绪 preset，例如 `专业温暖` |
| Source | `VoiceAgent` / imported / digital-human / storyboard |
| Trace | 打开 generation sidecar、dialogue spec、exact payload |

卡片动作：

```text
Play / Pause
查看脚本
查看生成参数
复制 Prompt Package
发送到数字人
发送到视频生成音频
删除
```

### 3.4 右侧对话模式

建议做成紧凑 segmented control：

```text
目的生成 / 脚本优化 / 多角色对话 / 情绪增强 / Provider适配
```

其中“目的生成”是默认模式。

用户可以输入：

```text
做一段 35 秒的双人播客开场，一个主持人热情，一个专家稳重，主题是介绍 OpenCrew 的视频生产能力。
```

Agent 输出：

1. 角色设定
2. 多轮脚本
3. 情绪与节奏标注
4. Gemini prompt
5. ElevenLabs dialogue inputs
6. 生成确认按钮
7. 生成完成后，中间 Voice Asset Library 自动新增音频卡片

## 4. Provider 能力映射

### 4.1 Gemini TTS

Gemini TTS 的工作方式偏“自然语言提示词 + speaker voice 配置”。适合：

1. 用自然语言描述整体语气、情绪、节奏。
2. 生成单人或多说话人音频。
3. 让模型理解上下文并自然演绎。

建议内部规范化为：

```json
{
  "provider": "google",
  "model": "gemini-3.1-flash-tts-preview",
  "mode": "multi_speaker",
  "global_direction": "You are producing a natural Mandarin podcast dialogue...",
  "speakers": [
    {
      "speaker_id": "host",
      "display_name": "主持人",
      "voice": "Kore",
      "emotion_profile": "warm, bright, curious"
    },
    {
      "speaker_id": "expert",
      "display_name": "专家",
      "voice": "Charon",
      "emotion_profile": "calm, trustworthy, concise"
    }
  ],
  "script": [
    {
      "speaker_id": "host",
      "emotion": "excited",
      "pace": "medium-fast",
      "text": "欢迎来到今天的节目..."
    }
  ]
}
```

最终 Gemini prompt 可以合成为：

```text
You are producing a natural Mandarin two-speaker podcast dialogue.
Speaker host uses voice Kore. Tone: warm, bright, curious.
Speaker expert uses voice Charon. Tone: calm, trustworthy, concise.
Keep speaker turns clear. Use natural pauses. Do not read speaker labels aloud.

[host, excited, medium-fast] 欢迎来到今天的节目...
[expert, calm, medium] 是的，OpenCrew 解决的是...
```

注意：当前 `generate_google_tts_audio()` 只传单个 `voice_id` 到 `prebuiltVoiceConfig`。P0 如果要支持 Gemini 真正多 speaker，需要新增多 speaker speechConfig/payload builder，不能只沿用现有单 voice helper。

### 4.2 ElevenLabs Text to Dialogue

ElevenLabs 的工作方式偏“结构化 dialogue turns + 每轮 voice_id”。适合：

1. 明确控制每一句由哪个 voice 说。
2. 多角色对话生成。
3. 使用内联情绪 tag，例如 `[excited]`、`[curious]`、`[whispers]`。

建议内部规范化为：

```json
{
  "provider": "elevenlabs",
  "model": "eleven_v3",
  "mode": "text_to_dialogue",
  "speakers": [
    {
      "speaker_id": "host",
      "display_name": "主持人",
      "voice_id": "JBFqnCBsd6RMkjVDRZzb",
      "emotion_profile": "bright, inviting"
    },
    {
      "speaker_id": "expert",
      "display_name": "专家",
      "voice_id": "Aw4FAjKCGjjNkVhN1Xmq",
      "emotion_profile": "calm, precise"
    }
  ],
  "inputs": [
    {
      "speaker_id": "host",
      "voice_id": "JBFqnCBsd6RMkjVDRZzb",
      "text": "[excited] 欢迎来到今天的节目..."
    },
    {
      "speaker_id": "expert",
      "voice_id": "Aw4FAjKCGjjNkVhN1Xmq",
      "text": "[calm] 是的，OpenCrew 解决的是..."
    }
  ]
}
```

ElevenLabs API 层建议直接调用：

```text
POST https://api.elevenlabs.io/v1/text-to-dialogue
```

请求体使用 `inputs[] = { text, voice_id }`。生成前要校验 unique voice_id 数量、总字符数和 provider 限制。

## 5. Voice Agent 能力边界

首版必须聚焦四件事：

1. 目的理解：把“我要做什么”转成声音任务 brief。
2. 脚本生成：生成多轮对话，不直接堆长旁白。
3. Provider 适配：同一份 dialogue spec 可导出 Gemini prompt 和 ElevenLabs inputs。
4. 音频生成：用户确认后调用 provider，生成音频并入库。

不要让首版同时承担数字人口型、视频合成、配乐混音和后期剪辑。这些可以作为生成后动作：

```text
生成音频 -> 发送到数字人
生成音频 -> 发送到视频生成 driving_audio
生成音频 -> 保存到 Asset Library
```

## 6. 提示词库设计

参考 PromptAgent 的知识库设计，新增 repo 级目录：

```text
OpenCrew/PromptKnowledge/
  normalized/
    voice/
      gemini/
      elevenlabs/
      dialogue_structure/
      emotion_control/
      voice_casting/
      local_experience/
```

Voice Agent 的 Session 运行痕迹：

```text
SessionContext/VoiceAgent/
  ChatState.json
  Drafts/
    Draft_<request_id>_VoiceDialogue.json
    Draft_<request_id>_GeminiPrompt.txt
    Draft_<request_id>_ElevenLabsInputs.json
  Applied/
    Applied_<request_id>_VoicePackage.json
  Generations/
    voicegen_<generation_id>.json
    voicegen_<generation_id>.wav
    voicegen_<generation_id>.mp3
  Retrieval/
    retrieval_<request_id>.json
  Sources/
    Ref_TTS_Gemini.md
    Ref_TTS_ElevenLabs.md
```

### 6.1 Voice Prompt Rule

知识库不只存 provider 文档，也要沉淀可执行规则：

```json
{
  "rule_id": "voice_dialogue_turn_length",
  "provider": "general",
  "rule_type": "do",
  "text": "每轮对话建议 1-2 句，短句更容易控制情绪和节奏。",
  "applies_to": ["gemini", "elevenlabs"],
  "confidence": 0.86
}
```

### 6.2 情绪标签库

新增统一 emotion schema，再映射到 provider：

```json
{
  "emotion_id": "warm_excited",
  "label": "热情但不夸张",
  "gemini_instruction": "sound warm, bright, lightly excited, not salesy",
  "elevenlabs_tag": "[excited]",
  "pace": "medium-fast",
  "energy": 0.72,
  "safe_note": "避免全程高能，关键句加强即可"
}
```

建议首批内置：

```text
calm_trustworthy
warm_excited
curious
empathetic
serious
confident
playful
gentle
urgent
reflective
```

### 6.3 角色模板库

```json
{
  "role_template_id": "podcast_host_expert",
  "label": "主持人 + 专家",
  "roles": [
    {"speaker_id": "host", "persona": "引导、提问、总结", "default_emotion": "warm_excited"},
    {"speaker_id": "expert", "persona": "解释、判断、给结论", "default_emotion": "calm_trustworthy"}
  ],
  "best_for": ["podcast", "product_explainer", "training"]
}
```

## 7. Voice Agent 内部数据模型

### 7.1 Voice Brief

用户目的先被转成结构化 brief：

```json
{
  "schema_version": "koubo_voice_brief_0.1",
  "request_id": "voice_agent_...",
  "purpose": "介绍 OpenCrew 的视频生产能力",
  "scenario": "podcast_intro",
  "language": "zh-CN",
  "duration_seconds": 35,
  "speaker_count": 2,
  "audience": "企业客户",
  "style": "专业、轻松、可信",
  "must_include": ["OpenCrew", "视频生产", "可复用智能体"],
  "must_avoid": ["夸张营销腔", "读出角色标签"]
}
```

### 7.2 Dialogue Spec

所有 provider 都先走统一 dialogue spec：

```json
{
  "schema_version": "koubo_voice_dialogue_spec_0.1",
  "request_id": "voice_agent_...",
  "speakers": [
    {
      "speaker_id": "host",
      "name": "主持人",
      "gender_hint": "female",
      "persona": "热情引导",
      "emotion_default": "warm_excited",
      "provider_voice": {
        "google": "Kore",
        "elevenlabs": "JBFqnCBsd6RMkjVDRZzb"
      }
    }
  ],
  "turns": [
    {
      "turn_id": "t001",
      "speaker_id": "host",
      "text": "欢迎来到今天的节目...",
      "emotion": "warm_excited",
      "pace": "medium-fast",
      "pause_after_ms": 300,
      "emphasis": ["OpenCrew"]
    }
  ]
}
```

### 7.3 Provider Package

点击“生成”前必须生成 provider package：

```json
{
  "schema_version": "koubo_voice_provider_package_0.1",
  "request_id": "voice_agent_...",
  "provider": "elevenlabs",
  "model": "eleven_v3",
  "dialogue_spec_path": "SessionContext/VoiceAgent/Drafts/Draft_..._VoiceDialogue.json",
  "payload": {
    "inputs": [
      {"text": "[excited] 欢迎来到今天的节目...", "voice_id": "JBFqnCBsd6RMkjVDRZzb"}
    ],
    "model_id": "eleven_v3",
    "language_code": "zh"
  },
  "exact_prompt_preview": "...",
  "created_at": "2026-06-23T00:00:00+08:00"
}
```

## 8. Agent 输出格式

### 8.1 目的生成模式

Agent 必须返回结构化结果，前端按卡片渲染：

```json
{
  "mode": "purpose_to_voice",
  "brief": {},
  "speakers": [],
  "dialogue": [],
  "emotion_plan": [],
  "gemini_prompt": "",
  "elevenlabs_inputs": [],
  "model_notes": [
    "Gemini 更适合整体表演控制；ElevenLabs 更适合逐轮 voice_id 控制。"
  ],
  "next_actions": [
    {"type": "save_draft", "label": "保存草稿"},
    {"type": "generate_gemini", "label": "用 Gemini 生成"},
    {"type": "generate_elevenlabs", "label": "用 ElevenLabs 生成"}
  ]
}
```

### 8.2 脚本优化模式

```json
{
  "mode": "script_optimize",
  "issues": [
    {
      "severity": "medium",
      "turn_id": "t003",
      "problem": "这一轮台词过长，情绪控制容易变平。",
      "suggestion": "拆成两轮，先解释价值，再给例子。"
    }
  ],
  "revised_dialogue": [],
  "emotion_changes": [],
  "provider_notes": []
}
```

### 8.3 Provider 适配模式

```json
{
  "mode": "provider_adapt",
  "gemini": {
    "prompt": "",
    "voices": [],
    "warnings": []
  },
  "elevenlabs": {
    "inputs": [],
    "voices": [],
    "warnings": []
  }
}
```

## 9. 后端接口设计

### 9.1 Agent Chat

建议沿用通用 Agent Chat 风格：

```text
POST /api/koubo-storyboard/tasks/{task_id}/agents/voice_agent/chat/ensure-session
GET  /api/koubo-storyboard/tasks/{task_id}/agents/voice_agent/chat/messages
POST /api/koubo-storyboard/tasks/{task_id}/agents/voice_agent/chat/message
GET  /api/koubo-storyboard/tasks/{task_id}/agents/voice_agent/chat/events
POST /api/koubo-storyboard/tasks/{task_id}/agents/voice_agent/chat/abort
```

`agent_chat_routes.py` 增加：

```python
AGENT_CHAT_KEYS += {"voice_agent"}
AGENT_CHAT_TITLES["voice_agent"] = "Koubo Voice Agent"
```

### 9.2 Prompt Builder / Package Builder

新增：

```text
POST /api/koubo-storyboard/tasks/{task_id}/voice-agent/build
```

请求：

```json
{
  "mode": "purpose_to_voice",
  "purpose": "35 秒双人播客开场...",
  "provider_targets": ["google", "elevenlabs"],
  "language": "zh-CN",
  "duration_seconds": 35,
  "speaker_count": 2,
  "style": "professional_warm",
  "constraints": {}
}
```

响应：

```json
{
  "ok": true,
  "request_id": "voice_agent_...",
  "brief_path": "SessionContext/VoiceAgent/Drafts/Draft_..._Brief.json",
  "dialogue_spec_path": "SessionContext/VoiceAgent/Drafts/Draft_..._VoiceDialogue.json",
  "gemini_prompt_path": "SessionContext/VoiceAgent/Drafts/Draft_..._GeminiPrompt.txt",
  "elevenlabs_inputs_path": "SessionContext/VoiceAgent/Drafts/Draft_..._ElevenLabsInputs.json",
  "preview": {}
}
```

### 9.3 生成音频

新增：

```text
POST /api/koubo-storyboard/tasks/{task_id}/voice-agent/generate/events
```

请求：

```json
{
  "request_id": "voice_agent_...",
  "provider": "elevenlabs",
  "model": "eleven_v3",
  "package_path": "SessionContext/VoiceAgent/Applied/Applied_..._VoicePackage.json",
  "output_format": "mp3_44100_128",
  "save_to_asset_library": true
}
```

SSE 事件：

```text
start
package_validated
provider_requested
audio_received
asset_saved
done
error
```

### 9.4 历史与版本

```text
GET /api/koubo-storyboard/tasks/{task_id}/voice-agent/versions
PUT /api/koubo-storyboard/tasks/{task_id}/voice-agent/versions/{request_id}
GET /api/koubo-storyboard/tasks/{task_id}/voice-agent/generations
```

生成 sidecar 必须记录：

```json
{
  "schema_version": "koubo_voice_generation_0.1",
  "generation_id": "voicegen_...",
  "request_id": "voice_agent_...",
  "provider": "elevenlabs",
  "model": "eleven_v3",
  "dialogue_spec_path": "...",
  "package_path": "...",
  "exact_provider_payload": {},
  "audio_path": "SessionContext/VoiceAgent/Generations/voicegen_....mp3",
  "asset_library_path": "audio/voicegen_....mp3",
  "created_at": "..."
}
```

## 10. Provider Service 设计

不要继续把 provider 逻辑塞进 `router.py`。建议新增：

```text
OpenCrew/backend/opcrew_backend/koubo/koubo_storyboard/voice_agent_routes.py
OpenCrew/backend/opcrew_backend/koubo/koubo_storyboard/voice_agent_services.py
OpenCrew/backend/opcrew_backend/koubo/koubo_storyboard/voice_provider_services.py
```

Provider service 对外只暴露：

```python
generate_voice_audio(provider_config, package, output_path) -> VoiceGenerationResult
```

内部按 provider 分发：

```text
generate_gemini_dialogue_audio()
generate_elevenlabs_dialogue_audio()
```

### 10.1 Gemini Service

职责：

1. 从 provider package 生成 Gemini request body。
2. 支持单 speaker 和 multi speaker。
3. 写入 wav/mp3。
4. 记录 exact payload。

P0 可以先支持单文件输出；P1 再支持分 turn 时间戳或分段试听。

### 10.2 ElevenLabs Service

职责：

1. 校验 `inputs[]`、`voice_id`、`model_id`。
2. 调用 `/v1/text-to-dialogue`。
3. 保存 mp3/wav。
4. 支持 `enable_logging=false` 的企业零保留模式配置。

P0 不做 voice clone，只使用已有 voice_id。P1 再加入 voice library / clone 管理。

## 11. 前端实现设计

### 11.1 新增 View

修改：

```text
OpenCrew/frontend/src/modules/koubo/UploadAssetLibrary/UploadAssetLibraryOverlay.jsx
```

新增：

```js
const LIBRARY_VIEWS = new Set([
  "images",
  "images-agent",
  "videos",
  "videos-agent",
  "digital-human-agent",
  "voice-agent",
  "history",
]);
```

### 11.2 新增 Sidebar 项

修改：

```text
OpenCrew/frontend/src/modules/koubo/UploadAssetLibrary/components/LibrarySidebar.jsx
```

新增按钮：

```jsx
<button class={props.view() === "voice-agent" ? "is-active" : ""} type="button" onClick={() => props.setView("voice-agent")}>
  <span class="ual-nav-icon"><FlowIcon name="record_voice_over" /></span>
  <span class="ual-nav-label">语音智能体</span>
</button>
```

### 11.3 新增组件

```text
OpenCrew/frontend/src/modules/koubo/UploadAssetLibrary/voiceAgent/
  VoiceAssetLibraryView.jsx
  VoiceAssetCard.jsx
  VoiceAssetPreviewDrawer.jsx
  VoiceAgentPanel.jsx
  VoiceAgentSettings.jsx
  VoiceProviderPreview.jsx
  VoiceGenerationHistory.jsx
  voiceAgent.css
```

组件职责：

| 组件 | 职责 |
| --- | --- |
| `VoiceAssetLibraryView.jsx` | 中间主区域，展示可试听的 Voice/Audio asset 网格、列表、筛选和空状态 |
| `VoiceAssetCard.jsx` | 单个音频卡片，提供播放、时长、provider、角色、来源和操作按钮 |
| `VoiceAssetPreviewDrawer.jsx` | 从中间卡片打开，查看脚本、dialogue spec、provider package、exact payload |
| `VoiceAgentPanel.jsx` | 右侧对话区，所有目的输入、脚本生成、情绪调整、provider 适配和生成确认都在这里完成 |
| `VoiceAgentSettings.jsx` | 右侧对话的紧凑设置，不占用中间资产区域 |
| `VoiceProviderPreview.jsx` | 右侧对话消息中的 provider package 预览卡片 |
| `VoiceGenerationHistory.jsx` | 右侧对话/中间资产库都可复用的生成历史摘要 |

不要在中间区域放常驻角色表或常驻脚本编辑器。角色、脚本、情绪和 provider package 都作为右侧 Agent 对话产物展示；用户点击中间音频卡片时，只以详情抽屉方式查看。

### 11.4 右侧对话控件

| 控件 | 类型 | 默认 |
| --- | --- | --- |
| Provider | segmented control | Gemini |
| Model | select | 当前 provider 默认模型 |
| Voice mode | segmented control | Multi speaker |
| Language | select/input | zh-CN |
| Duration | number input / slider | 30 秒 |
| Role count | stepper | 2 |
| Emotion preset | menu | 专业温暖 |
| Output format | select | mp3 |
| Generate | primary icon+text button | 生成音频 |

右侧 Agent 生成的角色草稿展示为消息卡片，不作为中间主区域：

```text
角色名 | persona | voice | 默认情绪 | 试听
```

右侧 Agent 生成的多轮脚本展示为消息卡片，可在卡片内轻量编辑：

```text
speaker | emotion | pace | text | pause
```

生成成功后，消息卡片显示“已保存到 Voice Asset Library”，中间区域同时新增对应音频卡片。

## 12. Voice Agent System Prompt 建议

```text
你是 Koubo Asset Library 的语音智能体。

你的任务是把用户的目的转成可生成的多角色 TTS 方案，并适配 Gemini TTS 和 ElevenLabs Text to Dialogue。

规则：
1. 先理解用户目的、场景、听众、时长、语言和角色数量。
2. 输出多轮对话，而不是单段长旁白，除非用户明确要求旁白。
3. 每个角色必须有 persona、默认情绪、声音建议和说话职责。
4. 每轮台词必须短、清晰、便于 TTS 控制情绪。
5. 情绪只在关键句增强，不要全程高强度。
6. Gemini 输出自然语言导演提示和多 speaker 脚本；ElevenLabs 输出 inputs[]，每条包含 voice_id 和带情绪 tag 的 text。
7. 不读出角色标签、emotion 标签、stage direction。
8. 若信息不足，先用合理默认值生成可编辑草稿。
9. 输出必须包含 brief、speakers、dialogue、emotion_plan、provider_packages、warnings、next_actions。
```

## 13. 实施顺序

### P0：设计闭环可用

1. 新增左侧“语音智能体”入口。
2. 新增中间 Voice Asset Library 视图和右侧 Voice Agent 对话面板。
3. 新增 Voice brief -> dialogue spec -> provider package 的本地构建能力。
4. Gemini 支持从 provider package 生成音频。
5. ElevenLabs 新增 provider config 和 text-to-dialogue 生成能力。
6. 生成结果写入 `SessionContext/VoiceAgent/Generations/` 并显示在 Asset Library audio 区。

### P1：提示词库和可控性

1. 新增 `PromptKnowledge/normalized/voice/`。
2. 导入 Gemini / ElevenLabs 官方规则、本地经验、情绪标签库、角色模板库。
3. 支持“目的生成 / 脚本优化 / 情绪增强 / Provider适配”。
4. 支持每个角色试听 voice catalog。
5. 支持保存成功案例到 `local_experience`。

### P2：生产质量

1. 支持分 turn 试听和局部重生成。
2. 支持音频自动响度标准化和静音裁剪。
3. 支持导出 SRT/turn timing，用于视频或数字人口型。
4. 支持把生成音频一键发送到数字人 Agent。
5. 支持多 provider A/B 对比和人工评分闭环。

## 14. 验收标准

### P0 验收

1. 左侧出现“语音智能体”，位置在“数字人智能体”下方。
2. 输入目的后能生成多角色、多轮对话草稿。
3. 同一个 dialogue spec 能导出 Gemini prompt 和 ElevenLabs inputs。
4. 用户确认后能调用至少一个 provider 生成音频。
5. 生成 sidecar 记录 exact provider payload、provider、model、voice、prompt/package 路径。
6. 结果音频能在 Asset Library 中作为 audio asset 使用。
7. 不影响现有图像生成、视频生成、数字人智能体、Prompt Builder。

### P1 验收

1. 支持从提示词库检索 Gemini / ElevenLabs 的规则和本地经验。
2. 支持角色模板和情绪 preset。
3. 支持按 turn 调整 speaker、emotion、pace、pause。
4. 同一目的选择 Gemini 和 ElevenLabs 时，输出 package 明显符合各自 provider 特性。
5. 所有 Draft / Applied / Generation 均可追溯。

## 15. 关键设计结论

1. Voice Agent 应该是独立入口，不要塞进数字人智能体。
2. 核心中间层必须是 provider-neutral 的 `dialogue_spec`。
3. Gemini 适合自然语言导演式控制；ElevenLabs 适合逐轮 voice_id 和情绪 tag 控制。
4. Prompt Builder 的经验可以复用，但 Voice Agent 必须拥有独立的 `SessionContext/VoiceAgent/` 审计目录。
5. 首版先生成单个最终音频文件；分 turn 重生成、时间戳、口型联动放到 P1/P2。
6. Provider 调用必须记录 exact payload，方便回答“这段音频到底由哪个提示词、哪个 voice、哪个模型生成”。
