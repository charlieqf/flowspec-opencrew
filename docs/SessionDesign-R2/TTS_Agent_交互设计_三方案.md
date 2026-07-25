# TTS Agent 交互设计三方案

## 1. 设计目标

设计一个 `TTS-Agent`，让用户能在 OpenCrew 中用最少编辑完成：

1. 单人 TTS 提示词撰写。
2. 双角色 / 多角色脚本整理。
3. Gemini TTS 音色、角色、风格、语速、情绪标签的快速配置。
4. 生成可试听、可验证、可复用的 TTS 文件。

页面形态参考现有 `Images-Agent` / `Videos-Agent`：

- 右侧 Agent 对话区。
- 底部 Composer 统一输入框。
- Settings 统一从 Connection / Model Config 读取模型池。
- Agent 输出结构化生成请求，前端展示确认卡。
- 生成结果落盘，并写 sidecar JSON / manifest 供回放、验证和复用。

## 2. Gemini TTS API 要点

Gemini TTS 官方文档当前推荐 Interactions API，但本地现有实现仍使用 Generate Content API。两者都支持单说话人和最多 2 个说话人的 TTS。

关键约束：

1. TTS 输入只接受文本，输出只生成音频。
2. Gemini TTS 可用自然语言控制 style、tone、accent、pace。
3. 多说话人最多 2 个 speaker。
4. 多说话人配置里的 `speaker` 名称必须与 prompt / transcript 中的说话人名一致。
5. Gemini 3.1 Flash TTS Preview 支持 streaming；其它 TTS 模型 streaming 受限。
6. 长音频容易漂移，几分钟以上建议拆分。
7. prompt 必须明确标注“合成语音”和“实际朗读正文”，避免模型把导演笔记读出来。
8. 偶发 500 / 文本 token 返回需要自动重试。

本地实现可继续沿用 Generate Content API 的字段：

```json
{
  "contents": [
    {
      "parts": [
        {
          "text": "最终 TTS prompt"
        }
      ]
    }
  ],
  "generationConfig": {
    "responseModalities": ["AUDIO"],
    "speechConfig": {
      "voiceConfig": {
        "prebuiltVoiceConfig": {
          "voiceName": "Kore"
        }
      }
    }
  }
}
```

多说话人：

```json
{
  "generationConfig": {
    "responseModalities": ["AUDIO"],
    "speechConfig": {
      "multiSpeakerVoiceConfig": {
        "speakerVoiceConfigs": [
          {
            "speaker": "小林",
            "voiceConfig": {
              "prebuiltVoiceConfig": {
                "voiceName": "Kore"
              }
            }
          },
          {
            "speaker": "小周",
            "voiceConfig": {
              "prebuiltVoiceConfig": {
                "voiceName": "Puck"
              }
            }
          }
        ]
      }
    }
  }
}
```

## 3. 本地可复用案例

当前 Connection / Model Config 中已经有 TTS 场景字典，可以直接升级为 TTS-Agent 的场景模板：

| 场景 | 现有用途 | Agent 可复用方式 |
| --- | --- | --- |
| 单说话人基础朗读 | 验证 VoiceConfig、清晰度、稳定性 | 一键生成旁白 prompt |
| 单说话人风格控制 | 验证 style / tone / pace | 口播、商业旁白、短视频开场 |
| 情绪标签控制 | 验证 `[excitedly]`、`[bored]`、`[whispers]` | 局部情绪编辑器 |
| 局部语气切换 | 验证低语、强调、回落 | 分句标签补全 |
| 速度控制 | 验证 `[very fast]` / `[very slow]` | 语速策略模板 |
| 非语言声音 | 验证 `[sighs]`、`[gasp]`、`[laughs]` | 表演标记插入 |
| 多说话人对话 | 验证最多 2 speaker、speaker 名称匹配 | 双角色脚本生成 |
| 多说话人角色差异 | 验证 speaker-specific guidance | 主持人 / 嘉宾、甲 / 乙 |
| 播客脚本 | 验证两人播客 transcript | 播客 / 访谈类一键模板 |
| 有声书旁白 | 验证高级提示、Scene、Director's Notes | 长叙事拆段生成 |

本地已有的重要工程约束：

1. Prompt 与正文必须隔离。
2. `storyboard_tts_selection.prompt` 只能作为风格指令，不可信任其旧正文。
3. 生成前必须清理旧的 `正文:`、`朗读文本:`、`Text:` 后面的历史内容。
4. 最终 prompt 必须是“清理后的风格指令 + 当前 Dialogue 正文”。
5. `config_key` 必须包含 Dialogue 身份、正文、provider、model、voice、prompt、tempo。
6. 不能只用输出路径判断缓存命中。

## 4. 方案一：Composer 快速生成型

### 适用场景

最快生成一段 TTS，适合单人旁白、短视频口播、广告开场、简短双人对话。

### 页面方式

新增 `Asset Library > Audios-Agent` 或 `TTS-Agent` 右栏，复用 `Images-Agent` / `Videos-Agent` 的右栏结构：

- Header：`TTS-Agent`
- Settings：读取 TTS Connection 模型池。
- Composer 工具栏：
  - 上传参考音频。
  - 打开场景模板。
  - 打开 Voice Picker。
  - 打开 Settings。
  - Generate。
- 输入框 placeholder：

```text
输入要朗读的文案，或描述你想要的声音表演。
```

### 用户路径

1. 用户输入一句自然语言：

```text
帮我生成一段双人播客风格的 TTS，小林沉稳，小周活泼，讨论 OpenCrew 的 TTS Agent。
```

2. Agent 自动识别：

- `mode = multi_speaker`
- `speaker_count = 2`
- `speaker_1 = 小林`
- `speaker_2 = 小周`
- `voice_1 = Kore`
- `voice_2 = Puck`
- `scenario = podcast-transcript`

3. Agent 输出确认卡：

```text
TTS 生成请求
模式：双说话人
模型：Gemini 3.1 Flash TTS Preview
小林：Kore / 沉稳、清晰、可信
小周：Puck / 活泼、轻快、自然接话
预计输出：1 个 WAV 文件
```

4. 用户点 Generate，生成音频和 manifest。

### Agent 输出协议

Agent 消息内嵌结构化请求：

```xml
<TTS_GENERATION_REQUEST>
{
  "title": "双人播客开场",
  "mode": "multi_speaker",
  "provider": "google",
  "model": "gemini-3.1-flash-tts-preview",
  "language": "cmn",
  "speakers": [
    {
      "speaker": "小林",
      "voice": "Kore",
      "style": "沉稳、清晰、可信"
    },
    {
      "speaker": "小周",
      "voice": "Puck",
      "style": "活泼、轻快、自然接话"
    }
  ],
  "prompt": "最终 prompt",
  "transcript": "小林: ...\n小周: ...",
  "confirm": true
}
</TTS_GENERATION_REQUEST>
```

### 优点

- 最快。
- 和现有 Agent 页面一致。
- 用户不需要先理解 Gemini TTS 字段。

### 缺点

- 对长脚本、多轮角色编辑不够精细。
- 多角色超过 2 人时必须提示拆分或改成旁白转述。

## 5. 方案二：角色表 + 脚本编辑型

### 适用场景

多角色 TTS、双人播客、访谈、短剧、客户对话、需要精细控制 speaker 的场景。

### 页面方式

在 `TTS-Agent` 中增加一个可编辑的 `角色表 + Transcript` 面板，类似视频生成的模型设置 + Prompt Builder，但更偏结构化。

界面分三块：

1. 角色表
2. 脚本区
3. 试听 / 验证区

角色表字段：

| 字段 | 说明 |
| --- | --- |
| Speaker | 角色名，必须和脚本行前缀一致 |
| Voice | Gemini voice |
| Persona | 年龄感、身份、音色方向 |
| Style | 语气 / 情绪 |
| Pace | 语速 |
| Tags | 常用标签 |

脚本区支持：

```text
小林: 今天我们先把 TTS Agent 的交互方式跑通。
小周: [excitedly] 好，我想先听双角色是不是能明显区分。
```

### 交互流程

1. 用户粘贴脚本。
2. Agent 自动识别角色。
3. 如果角色超过 2 个：
   - 提示 Gemini TTS 多 speaker 最多 2 个。
   - 给三种处理：
     - 只保留主说话人 A/B。
     - 分段生成多段音频。
     - 改写成旁白 + 一位角色。
4. 用户选择 voice。
5. Agent 自动检查：
   - speaker 名称是否一致。
   - prompt 是否把角色标签读出来。
   - 是否包含旧正文。
   - 是否有空行 / 无 speaker 行。
6. 生成 `TTS_GENERATION_REQUEST`。

### 字典设计

```json
{
  "scenario": "multi-dialogue",
  "mode": "multi_speaker",
  "speaker_limit": 2,
  "voices": {
    "Kore": {
      "label": "Kore",
      "tone": "firm",
      "best_for": ["narration", "serious", "clear"]
    },
    "Puck": {
      "label": "Puck",
      "tone": "upbeat",
      "best_for": ["excited", "host", "youthful"]
    },
    "Aoede": {
      "label": "Aoede",
      "tone": "breezy",
      "best_for": ["short_video", "friendly", "lifestyle"]
    }
  },
  "audio_tags": [
    "[excitedly]",
    "[bored]",
    "[whispers]",
    "[very fast]",
    "[very slow]",
    "[sighs]",
    "[gasp]",
    "[laughs]"
  ]
}
```

### 优点

- 对多角色最稳。
- 用户能快速编辑角色、声音、脚本。
- 适合做成正式生产工具。

### 缺点

- 比方案一多一步结构化编辑。
- 第一版只能严格支持 2 speaker，超过 2 speaker 需要拆段策略。

## 6. 方案三：Storyboard / 视频生成联动型

### 适用场景

从 StoryBoard、VideoPlan、Dialogue 队列直接生成 TTS，适合口播视频、对口型、视频生成链路。

### 页面方式

在视频生成页面 / StoryBoard 中新增 `TTS Agent` 按钮，不单独作为素材库入口，而是绑定当前范围：

- 当前 Dialogue
- 当前 Scene
- 当前 Shot
- 当前 Video Segment

点击后打开右侧 Agent 面板：

```text
TTS-Agent
当前范围：Scene 03 / Dialogue 02-04
```

Agent 自动带入：

- Dialogue 文本。
- `dialogue_asset_key`。
- 当前 TTS Connection provider / model / voice。
- 已选 Builder-G 候选风格。
- 当前 Tempo / fit_to_duration。
- 当前视频段目标时长。

### 交互流程

1. 用户在 StoryBoard 选择 Scene。
2. 点击 `TTS Agent`。
3. Agent 读取当前 Dialogue 队列，不让用户重新粘贴正文。
4. 用户只编辑风格：

```text
保持真人口播感，语速略快，但不要播音腔；第二句更兴奋一点。
```

5. Agent 生成逐 Dialogue prompt。
6. 每条 Dialogue 独立生成音频。
7. 写回：

```text
SessionOutput/storyboard/Working/{dialogue_asset_key}_Audio_Final.wav
SessionOutput/storyboard/tts_manifests/{dialogue_asset_key}_Audio_Final.json
```

### 核心原则

1. StoryBoard 模式不允许把整个 Scene 文本一次性生成。
2. 必须逐 Dialogue 生成，确保 `dialogue_asset_key` 不串。
3. 当前 Dialogue 正文只能来自 StoryBoard 数据，不来自 Agent prompt。
4. Agent 只生成风格指令和 provider request。
5. 生成后必须回写时长。

### 优点

- 最贴近视频生成链路。
- 可直接服务对口型、音画同步、TTS 时长回写。
- 继承现有 StoryBoard 的缓存、manifest、`config_key` 验证规则。

### 缺点

- 不适合作为自由脚本创作入口。
- 需要严格处理 Dialogue / Scene / Shot 范围，避免历史踩坑复现。

## 7. 推荐落地顺序

### P0：方案一 + 方案二的小闭环

先做独立 `TTS-Agent`：

1. 读取 TTS Connection 中的 Google / Gemini 模型和 voice。
2. 支持单人 / 双人模式。
3. 复用现有场景字典。
4. Agent 输出 `<TTS_GENERATION_REQUEST>`。
5. 前端展示确认卡。
6. 后端生成 WAV。
7. 写 sidecar JSON。

### P1：方案三联动 StoryBoard

1. 在 Dialogue / Scene 操作区加入 `TTS Agent`。
2. Agent 只编辑风格，不接管正文。
3. 按 Dialogue 逐条生成。
4. 写回 `Working/{dialogue_asset_key}_Audio_Final.wav`。
5. 刷新音频卡和时长。

### P2：高级验证与自动优化

1. 自动检测真实时长。
2. 对比目标时长。
3. 需要时触发 prompt tempo fix 或本地 atempo。
4. 支持一键重试。
5. 支持对比 2-3 个候选。

## 8. 最小验收矩阵

| ID | 覆盖目标 | 设置 | 验收 |
| --- | --- | --- | --- |
| TAG-01 | 单人基础朗读 | Google / Gemini / Kore | 生成 WAV；manifest 中 provider/model/voice 正确 |
| TAG-02 | 风格控制 | Puck / upbeat prompt | 输出不朗读导演笔记；只读 transcript |
| TAG-03 | 情绪标签 | Aoede / `[whispers]`、`[laughs]` | 音频有可感知局部变化 |
| TAG-04 | 双人对话 | Kore + Puck / 小林 + 小周 | speaker 配置与 transcript 名称一致 |
| TAG-05 | 超过 2 角色 | 三角色脚本 | UI 阻止直接生成，并给拆段方案 |
| TAG-06 | Connection 未保存 | 无 API key | 生成按钮不可用或返回明确错误 |
| TAG-07 | Prompt 正文隔离 | prompt 含旧 `正文:` | 最终朗读当前 transcript，不读旧正文 |
| TAG-08 | 缓存命中 | config_key 完全一致 | 复用音频，不重复调用 |
| TAG-09 | 缓存失效 | voice / prompt / text 变化 | 重新生成 |
| TAG-10 | StoryBoard 回写 | Dialogue TTS | `Audio.src`、`working_assets.audio.path`、`dialogue_asset_key` 一致 |

## 9. 最推荐方案

最便捷、最稳的组合是：

1. `TTS-Agent` 独立入口采用方案一，保证用户一句话生成。
2. 点击确认卡上的 `Edit Roles` 进入方案二，解决双角色精修。
3. StoryBoard / 视频生成页面采用方案三，只处理当前 Dialogue / Scene 的风格优化和生成回写。

这样用户有三个层级：

| 层级 | 用户心智 | 最少操作 |
| --- | --- | --- |
| 快速生成 | 我只想要一个音频 | 输入一句话，点 Generate |
| 角色精修 | 我要双人对话更像角色 | 改角色表和脚本 |
| 视频联动 | 我要当前视频片段的 TTS | 选 Scene，写风格，生成 |

## 10. 参考来源

- Gemini API Text-to-speech generation: https://ai.google.dev/gemini-api/docs/speech-generation
- Gemini Generate Content API TTS: https://ai.google.dev/gemini-api/docs/generate-content/speech-generation
- 本地场景字典：`OpenCrew/ModelConfig/frontend/src/tts/TTSConfigModal.tsx`
- 本地 Google TTS preview 调用：`OpenCrew/ModelConfig/backend/opcrew_model_config/media_model_config.py`
- 本地 StoryBoard TTS 约束：`OpenCrew/docs/SessionDesign-R2/STORYBOARD_OUTPUT_STRUCTURE.md`
- 本地 Agent 页面规范：`PRD/asset_library_layout_implementation_standard.md`
