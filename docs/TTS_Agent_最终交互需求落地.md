# TTS Agent 最终交互需求落地

参考演示文件：`OpenCrew/docs/TTS_Agent_操作动画演示.html`

## 1. 结论

本需求以当前 HTML 动画说明的操作和交互效果为参考，真实落地以 Koubo / Asset Library 中的 `语音智能体 / TTS Agent` 页面实现为准。目标使用方式是 `右侧 Agent 连续对话 + 中间角色表和 TTS Prompt 工作区 + 底部声音文件回写`。

TTS Agent 的核心目标不是让用户手写完整 Gemini TTS payload，而是让用户通过 Agent 和配置弹窗，快速生成、校正、试听并回写一段可用于 StoryBoard 的 TTS 音频，尤其支持双角色 / 多角色对话场景。

首版交付边界必须明确：

1. 首版要落在真实 Koubo / Asset Library 的 `语音智能体 / TTS Agent` 入口内，不是只交付独立静态 HTML；导航位置必须在 `视频智能体` 之后、`数字人智能体` 之前。
2. HTML 演示只作为交互节奏、布局和状态切换参考，不是生产功能、数据源或逐帧验收标准。
3. 首版必须支持完整可交互流程、真实 TTS provider 生成的可播放音频、StoryBoard 槽位选择与真实回写动作。
4. 当前页面暴露的每一个可点击按钮都必须调用真实前后端能力；如果缺少前置条件或 provider 配置，按钮必须禁用并显示原因，不允许用假成功、样例音频或模拟播放替代。
5. HTML 演示中的样例脚本、动画和自动播放只用于说明交互方式，不是首版功能豁免。

术语定义：

| 术语 | 本文含义 |
| --- | --- |
| Connection | OpenCrew 主导航中的模型配置入口，底层由 ModelConfig 提供 TTS provider / model / voice 配置。 |
| Google Scenario Lab | Connection / ModelConfig 中 Google / Gemini TTS 的场景提示词实验弹窗；在 Koubo / OCRebuild 语境中这是实际 UI 名称。 |
| Scenario Guide Registry | 首版需要抽出的共享情景和关键词字典数据源，供 Google Scenario Lab、TTS Agent 和演示共同消费。Registry 必须放在 ModelConfig 或共享层，不能放在 Koubo 私有目录里让 ModelConfig 反向依赖 Koubo。 |
| HTML 演示 | `docs/TTS_Agent_操作动画演示.html` 中的静态动画，只说明交互节奏和布局，不作为生产数据源或功能替代。 |
| 真实试听 | 调用已配置 TTS provider 或 StoryBoard TTS API 生成真实音频文件后播放。 |

最终推荐形态：

1. 右侧 Agent 接收自然语言需求。
2. 中间工作区从空白开始，逐条生成角色表和 TTS Prompt。
3. 角色有独立的 `角色配置`。
4. 每句 Dialogue 有独立的 `Prompt 配置` 和单句播放。
5. 配置弹窗参考 Google Scenario Lab：先选情景，再看该情景的关键词字典。
6. 生成声音文件后，只展示可播放音频和 StoryBoard 槽位回写，不展示冗余 JSON 和多步骤状态卡。

## 2. 用户目标

用户希望在不知道 Gemini TTS 提示词怎么写的情况下，也可以快速完成：

1. 把自然语言需求转成角色表。
2. 把脚本逐句转成最终可生成声音的 TTS Prompt。
3. 快速调整某个角色的长期声音人格。
4. 快速调整某一句 Dialogue 的局部读法。
5. 试听单句和整段音频。
6. 把最终声音资产绑定回 StoryBoard 对应槽位。

## 3. 页面布局

### 3.1 顶部

演示稿顶部保留：

| 元素 | 行为 |
| --- | --- |
| Reset | 重置演示和工作区状态，仅用于参考稿 |
| 自动播放演示 | 模拟完整 TTS Agent 使用过程，仅用于参考稿 |

真实页面不强制提供 `Reset` 或 `自动播放演示` 按钮；真实验收看用户能否在页面内手动完成同样的生成、配置、试听和回写流程。顶部不再展示冗余模型状态、Session 说明或上下文路径。模型读取逻辑由 Agent 对话说明，不需要常驻在顶部。

### 3.2 左侧导航

左侧导航用于表达当前入口：

```text
Asset Library
- 图像生成
- 图像智能体
- 视频生成
- 视频智能体
- 语音智能体 / TTS Agent
- 数字人智能体
- 提示词智能体
- 素材检索智能体

Bottom
- History
- Collapse
```

当前激活入口为 `语音智能体 / TTS Agent`。这是新增入口，必须放在 `视频智能体` 和 `数字人智能体` 之间，避免把 TTS Agent 继续表达成旧的音频入口独立分组。

### 3.3 中间工作区

中间工作区包含两个主要区域：

1. `角色表 + TTS Prompt`
2. `声音文件`

`角色表 + TTS Prompt` 初始必须为空白，不允许页面一开始就显示完整列表。

初始空态：

```text
等待 Agent 生成角色表与 TTS Prompt
```

自动播放或真实 Agent 执行时，必须按顺序逐条生成：

1. 角色表出现。
2. 角色 1 出现。
3. 角色 2 出现。
4. Dialogue 01 Prompt 出现。
5. Dialogue 02 Prompt 出现。
6. Dialogue 03 Prompt 出现。

### 3.4 右侧 Agent

右侧 Agent 是唯一的自然语言输入和连续反馈区域。

右侧保留：

| 元素 | 行为 |
| --- | --- |
| Agent 消息流 | 展示识别、生成、配置、生成音频、回写等过程 |
| Composer 草稿 | 用户输入 TTS 需求 |
| 发送按钮 | 提交用户 TTS 需求，触发真实 StoryBoard 数据读取和可执行工作区生成 |

中间区域不再放大段输入框，不再复制右侧 Composer。

## 4. 核心流程

### 4.1 初始输入

用户在右侧 Composer 输入：

```text
帮我生成一段双人播客风格的 TTS，小林沉稳，小周活泼，讨论 OpenCrew 的 TTS Agent。
```

Agent 识别：

| 字段 | 识别结果 |
| --- | --- |
| mode | multi_speaker |
| speaker_count | 2 |
| language | Mandarin |
| provider | 从 ModelConfig active TTS 配置读取 |
| model | 从 ModelConfig active TTS 配置读取 |
| Speaker A | 小林 / Kore |
| Speaker B | 小周 / Puck |

规则：

1. provider / model / voice 只能来自 ModelConfig 当前配置，不允许在生产交互层写死。
2. UI 可以显示当前配置的模型名，但不能把 `gemini-3.1-flash-tts-preview` 或任何 preview 模型写成业务常量。
3. 如果 ModelConfig 没有可用 TTS 配置，生成和播放类按钮必须禁用并显示原因，不能静默伪装成真实生成。

### 4.2 逐条生成角色表

角色表字段：

| 字段 | 说明 |
| --- | --- |
| Speaker | 角色名，必须与 transcript speaker label 一致 |
| Voice | Gemini voice name |
| Style | 角色长期声音人格 |
| Pace | 角色长期节奏约束 |
| 操作 | `角色配置` |

示例：

| Speaker | Voice | Style | Pace |
| --- | --- | --- | --- |
| 小林 | Kore | 沉稳、清晰、可信 | 中速，句尾干净 |
| 小周 | Puck | 活泼、轻快、自然接话 | 略快但不抢话 |

### 4.3 逐条生成 TTS Prompt

TTS Prompt 是生成声音时的所见即所得输入。页面中显示的每一句 Prompt，就是后续生成音频时的准入文本。

首版必须区分两层：

| 层级 | 说明 |
| --- | --- |
| 单句可见 Prompt | 每张 Dialogue 卡片里展示的最终可见提示词，用户配置直接修改这一层。 |
| 整段组装 Prompt | 如果 provider 需要 multi-speaker 的导演提示 / transcript 结构，只能由单句可见 Prompt 和角色表确定性组装，并且必须在生成前以可见预览或可展开区域展示。 |

不得存在用户看不到、但实际送给 provider 的隐藏 prompt。

每个 Dialogue 卡片包含：

| 元素 | 行为 |
| --- | --- |
| 标题 | `小林 / Dialogue 01` |
| `Prompt 配置` | 只配置当前单句 Prompt |
| 播放按钮 | 调用真实 TTS 链路生成并播放当前单句 |
| Voice chip | 当前单句使用的 voice |
| Prompt 正文 | 当前单句最终 TTS prompt |

示例：

```text
沉稳、清晰、可信；中速，句尾干净；只朗读当前句：今天我们先把 TTS Agent 的交互方式跑通。
```

## 5. 配置能力

角色和单句都拥有提示词配置，但作用域不同。

### 5.1 角色配置

入口：

```text
角色表每行 > 角色配置
```

弹窗标题：

```text
角色提示词配置
```

作用域：

1. 更新当前角色的 `Style / Pace`。
2. 同步影响该角色对应的所有 TTS Prompt。
3. 不只修改某一句 Dialogue。

示例：

用户配置 `小周`：

| 字段 | 结果 |
| --- | --- |
| Style | vocal smile、自然口播、商业短视频 |
| Pace | 略快但清晰、短停顿、句尾干净 |
| 影响范围 | 小周所有 Dialogue |

### 5.2 单句 Prompt 配置

入口：

```text
每个 Dialogue 卡片 > Prompt 配置
```

弹窗标题：

```text
单句 Prompt 配置
```

作用域：

1. 只修改当前 Dialogue 的 TTS Prompt。
2. 不更新角色表。
3. 不影响同角色的其它 Dialogue。

示例：

用户配置 `Dialogue 02`：

| 字段 | 结果 |
| --- | --- |
| 情景 | 局部语气切换 |
| 关键词 | 先平稳、转为强调、收束放慢 |
| 影响范围 | 只影响 Dialogue 02 |

## 6. Voice Guide 弹窗

配置弹窗必须参考 Connection / ModelConfig 中 Google Scenario Lab 的使用方式。

首版必须把情景和关键词字典抽成单一数据源：

1. 新增或迁移一个共享的 `Scenario Guide Registry` 模块，优先放在 ModelConfig 或跨前端共享层，例如 `ModelConfig/frontend/src/tts/googleTtsScenarioGuide.ts`、`frontend/src/shared/tts/googleTtsScenarioGuide.js` 或等价共享包。
2. Google Scenario Lab 和 TTS Agent 页面都只消费该共享模块。
3. 当前真实共享 registry 落点为 `frontend/src/shared/tts/googleTtsScenarioGuide.ts`，Google Scenario Lab 与 TTS Agent 都必须从这里 import。
4. `docs/TTS_Agent_操作动画演示.html` 里的 `scenarioGuides` 只能作为参考稿数据；后续如继续维护演示稿，应从 registry 同步或校验，不得反向作为生产数据源。
5. 已迁移的 registry 必须保留 ModelConfig 原有 `multiSpeaker`、`verifies`、`buildComplexPrompt` 等元数据，不能只保留 TTS Agent 关键词字典。
6. 禁止让 ModelConfig 侧组件 import `frontend/src/modules/koubo/...` 这类上层业务模块；依赖方向必须是 Koubo 和 Google Scenario Lab 都向下依赖 registry。

### 6.1 弹窗结构

顶部字段：

| 字段 | 说明 |
| --- | --- |
| 当前角色 | 只读，显示当前配置作用到哪个角色 |
| 情景 | 下拉选择，来自 Scenario Guide Registry |
| Voice | 当前角色或当前单句使用的 Gemini voice |
| 角色提示词 / 单句 Prompt | 根据入口切换 label |

主体区域：

| 区域 | 内容 |
| --- | --- |
| 情景列表 | 展示所有官方 / 内置推荐情景 |
| 情景关键词字典 | 展示当前情景下的关键词分组 |

底部按钮：

| 模式 | 按钮文案 |
| --- | --- |
| 角色配置 | 套用到当前角色 |
| 单句 Prompt 配置 | 套用到当前单句 |

### 6.2 情景列表

首版 Scenario Guide Registry 必须包含以下情景：

| 情景 | 用途 |
| --- | --- |
| 单说话人基础朗读 | 清晰、稳定、少表演 |
| 单说话人风格控制 | 控制单角色整体气质 |
| 情绪标签控制 | 使用 `[excitedly]`、`[whispers]`、`[laughs]` 等标签 |
| 局部语气切换 | 当前句内部语气变化 |
| 速度控制 | 语速、停顿、清晰度控制 |
| 非语言声音 | 笑声、低语、呼吸等标签 |
| 多说话人对话 | 双角色 turn distinct |
| 多说话人角色差异 | 强化角色间差异 |
| 播客脚本 | 主持人 / 嘉宾自然对话 |
| 有声书旁白 | 慢速、画面感、叙事 |
| 广告 / 短视频旁白 | 抓人、略快、带笑意 |
| 口音控制 | Mandarin、标准普通话、轻微地域感 |
| 高级导演提示 | THE SCENE / DIRECTOR'S NOTES / TRANSCRIPT |
| 提示分类器规避 | 清晰转写边界、避免读出导演说明、降低提示误分类 |

### 6.3 情景关键词字典

切换情景时，右侧关键词字典必须实时变化。

全部 14 个情景都必须在 Scenario Guide Registry 中有完整关键词字典；本文只列两个示例，不代表只有两个场景需要字典。

示例：`广告 / 短视频旁白`

| 分组 | 关键词 |
| --- | --- |
| 风格 | vocal smile、自然口播、商业短视频、亲切真实 |
| 节奏 | 略快但清晰、短停顿、句尾干净、不抢话 |
| 音频标签 | `[excitedly]`、`[laughs]`、`[brightly]` |

示例：`局部语气切换`

| 分组 | 关键词 |
| --- | --- |
| 切换词 | 先平稳、转为强调、收束放慢、末尾确认 |
| 语气 | 提醒感、解释感、确认感、故事感 |
| 边界 | 只影响当前短句、不要改变角色整体声音 |

## 7. 单句播放

每个 Dialogue 卡片必须提供单句播放按钮。

行为：

1. 点击后调用真实 TTS 链路生成并播放当前 Dialogue。
2. 不要求先生成整段音频。
3. 当前卡片进入播放高亮态。
4. 不影响其它 Dialogue。

首版播放策略：

1. 优先复用现有 StoryBoard / AnalysisV1 TTS 生成链路，产出真实音频文件后播放。
2. 没有 provider 配置、当前 Dialogue 缺少 `dialogue_id` / `dialogue_asset_key`、或 provider 失败时，按钮必须禁用或进入错误态并显示原因。
3. 单句播放产出的音频必须是真实文件；如果该文件写入 `Audio_Final` 槽位，页面必须明确展示目标槽位和输出路径。

提示文案示例：

```text
正在播放 小周 / Puck Dialogue 02
```

## 8. 整段声音文件

生成整段 TTS 后，中间 `声音文件` 区域只展示必要内容：

| 元素 | 行为 |
| --- | --- |
| 播放按钮 | 播放真实生成的音频文件 |
| 文件名 | 展示生成的音频文件 |
| 路径 | 展示相对输出路径 |
| waveform | 简单可视化音频波形 |
| StoryBoard 槽位选择 | 选择回写目标 |
| 确认 StoryBoard 写回 | 确认当前音频已经真实写回目标槽位 |

首版必须使用真实 provider 生成结果：音频资产必须有文件名、相对路径、duration、provider、model、来源和目标槽位。没有真实输出时不得展示成功态。

不展示：

1. 复杂验证状态卡。
2. provider manifest JSON。
3. 多余 execution 状态列表。

### 8.1 回写 StoryBoard

用户试听满意后选择槽位：

```text
Scene 03 / Dialogue 02 / Audio Final
Scene 03 / Dialogue 03 / Audio Final
Scene 03 / Segment Audio
Shot 03 / Whole Shot Audio
```

点击：

```text
确认 StoryBoard 写回
```

状态更新为：

```text
已确认写回：Scene 03 / Dialogue 02 / Audio Final
```

槽位下拉展示人类可读路径，但提交和回写必须使用结构化 slot 对象。

示例：

```json
{
  "slot_key": "dak_0002:Audio_Final",
  "slot_type": "Audio_Final",
  "label": "Scene 03 / Dialogue 02 / Audio Final",
  "dialogue_id": "scene_03_dialogue_02",
  "dialogue_asset_key": "dak_0002",
  "scene_mark_id": "scene_03",
  "shot_id": "shot_03"
}
```

规则：

1. UI label 只用于显示，不参与推断。
2. 回写以 `slot_key` 和 `dialogue_asset_key` 为准。
3. Segment / Shot 级音频槽位也必须有明确 `slot_type` 和稳定 key。

## 9. 参考操作流程

HTML 里的自动播放只用于说明操作节奏。真实页面不要求自动驱动这些步骤，但用户手动操作时必须能完成同等流程：

1. 页面进入空白状态。
2. 右侧发送用户需求。
3. Agent 识别为双说话人 TTS。
4. 从 ModelConfig active TTS 配置读取 provider、model、voice。
5. 逐条生成角色表：
   - 小林 / Kore
   - 小周 / Puck
6. 逐条生成 TTS Prompt：
   - Dialogue 01
   - Dialogue 02
   - Dialogue 03
7. 打开小周的 `角色配置`。
8. 选择 `广告 / 短视频旁白` 情景。
9. 套用角色关键词字典，更新小周角色表和小周相关 Prompt。
10. 打开 Dialogue 02 的 `Prompt 配置`。
11. 选择 `局部语气切换` 情景。
12. 套用单句关键词字典，只更新 Dialogue 02。
13. 调用真实后端生成声音文件。
14. 展示真实音频文件、相对路径和播放入口。
15. 用户可试听并确认 StoryBoard 槽位写回。

## 10. 数据对象

### 10.1 Role

```json
{
  "speaker": "小周",
  "speaker_id": "speaker_b",
  "voice": "Puck",
  "style": ["vocal smile", "自然口播", "商业短视频"],
  "pace": ["略快但清晰", "短停顿", "句尾干净"],
  "role_revision": 2
}
```

### 10.2 Dialogue Prompt

```json
{
  "dialogue_id": "scene_03_dialogue_02",
  "dialogue_asset_key": "dak_0002",
  "speaker": "小周",
  "speaker_id": "speaker_b",
  "voice": "Puck",
  "source_text": "好，我想先听双角色是不是能明显区分。",
  "role_style": ["vocal smile", "自然口播", "商业短视频"],
  "role_pace": ["略快但清晰", "短停顿", "句尾干净"],
  "scenario": "局部语气切换",
  "scenario_keywords": ["先平稳", "转为强调", "收束放慢", "提醒感", "解释感", "确认感"],
  "inline_tags": ["[excitedly]"],
  "local_override": ["只影响当前短句", "不要改变角色整体声音"],
  "rendered_prompt": "先平稳、转为强调、收束放慢；提醒感，解释感，确认感；保留 [excitedly]；只朗读当前句：好，我想先听双角色是不是能明显区分。"
}
```

### 10.3 TTS Generation Request

```json
{
  "task_id": 135,
  "endpoint": "POST /api/koubo-storyboard/tasks/{task_id}/scene-tts/events",
  "provider": "ModelConfig active TTS provider",
  "model": "ModelConfig active TTS model",
  "model_source": "ModelConfig active TTS config",
  "mode": "single_dialogue_audio_final",
  "workflow_id": "tts_agent_scene_03_dialogue_02",
  "shot_id": "shot_03",
  "scene_mark_id": "scene_03",
  "dialogue_id": "scene_03_dialogue_02",
  "dialogue_asset_key": "dak_0002",
  "srt_text": "好，我想先听双角色是不是能明显区分。",
  "use_locked_cache": true,
  "locked_output": "SessionOutput/storyboard/Working/dak_0002_Audio_Final.wav",
  "locked_manifest": "SessionOutput/storyboard/tts_manifests/dak_0002_Audio_Final.json",
  "prompts": [
    {
      "provider": "ModelConfig active TTS provider",
      "model": "ModelConfig active TTS model",
      "voice_id": "ModelConfig active voice or selected role voice",
      "prompt": "先平稳、转为强调、收束放慢；提醒感，解释感，确认感；保留 [excitedly]；只朗读当前句：好，我想先听双角色是不是能明显区分。",
      "text": "好，我想先听双角色是不是能明显区分。",
      "user_instruction": "TTS Agent 真实生成。严格朗读当前 Dialogue 文本，不改词、不加词。",
      "tempo": 1
    }
  ],
  "prompt_source": "visible_tts_prompt_panel",
  "output_type": "wav",
  "storyboard_target": {
    "slot_key": "dak_0002:Audio_Final",
    "dialogue_asset_key": "dak_0002",
    "slot_type": "Audio_Final"
  }
}
```

### 10.4 Audio Asset

```json
{
  "asset_type": "audio",
  "filename": "dak_0002_Audio_Final.wav",
  "path": "SessionOutput/storyboard/Working/dak_0002_Audio_Final.wav",
  "duration_seconds": 8.42,
  "provider": "ModelConfig active TTS provider",
  "model": "ModelConfig active TTS model",
  "source": "TTS-Agent scene-tts/events",
  "generation_mode": "provider_generated",
  "storyboard_slot": {
    "slot_key": "dak_0002:Audio_Final",
    "slot_type": "Audio_Final",
    "label": "Scene 03 / Dialogue 02 / Audio Final",
    "dialogue_asset_key": "dak_0002"
  }
}
```

### 10.5 StoryBoard Slot

```json
{
  "slot_key": "dak_0002:Audio_Final",
  "slot_type": "Audio_Final",
  "label": "Scene 03 / Dialogue 02 / Audio Final",
  "dialogue_id": "scene_03_dialogue_02",
  "dialogue_asset_key": "dak_0002",
  "scene_mark_id": "scene_03",
  "shot_id": "shot_03",
  "target_path": "SessionOutput/storyboard/Working/dak_0002_Audio_Final.wav"
}
```

## 11. 工程规则

### 11.1 Prompt 与正文隔离

最终送给 TTS provider 的 prompt 必须明确区分：

1. 角色 / 导演指令。
2. 情景关键词。
3. 当前实际朗读正文。

不得把旧 prompt 中的历史正文带入当前 Dialogue。

实现要求：

1. `source_text` 必须独立保存，不从 `rendered_prompt` 反解析。
2. 角色配置和单句配置只能改提示词字段，不能改 `source_text`。
3. 重新渲染 Prompt 时必须从当前结构化字段生成，不能拼接旧 prompt 文本。

### 11.2 所见即所得

生成整段声音时，必须以中间 `TTS Prompt` 面板当前内容为准。

规则：

1. 用户看到的每句 Prompt，就是本次生成输入。
2. 角色配置后，受影响的 Prompt 必须实时更新。
3. 单句配置后，只更新当前 Prompt。
4. 如果需要整段 assembled prompt，必须由当前可见 Prompt 和角色表确定性生成，并在生成前可见。
5. 生成时不得另走一份用户不可见的隐藏 prompt。

### 11.3 多说话人限制

Gemini 多说话人首版按最多 2 个 speaker 处理。

超过 2 个 speaker 时：

1. Agent 必须提示拆段或合并角色。
2. 不允许静默提交不被 provider 支持的配置。

真实接入说明：

1. ModelConfig 的 TTS preview 已有可参考的 `multiSpeakerVoiceConfig / speakerVoiceConfigs` 思路。
2. 当前可见主流程使用 StoryBoard `scene-tts/events` 的真实单 Dialogue 生成与 `Audio_Final` 写回；页面不得把尚未接入的 Gemini multi-speaker 单次合成伪装成成功。
3. 如果后续要在页面上提供 Gemini multi-speaker 单次合成按钮，必须新增后端生成、缓存、错误处理、回写和合同测试后再暴露。

### 11.4 StoryBoard 回写

生成音频后必须可以选择 StoryBoard 槽位回写。

回写以 `dialogue_asset_key` / slot key 为准，不以文件名推断。

规则：

1. 页面展示 `label`，提交 `slot_key`。
2. Dialogue 级音频必须同时带 `dialogue_id` 和 `dialogue_asset_key`。
3. Segment / Shot 级音频必须带对应稳定 key，不能用当前显示文本或数组下标推断。
4. 生成音频的最终标准路径应由 slot 对象决定，例如 `SessionOutput/storyboard/Working/{dialogue_asset_key}_Audio_Final.wav`。
5. 当前 StoryBoard 代码和合同测试仍采用扁平 Working 路径：`SessionOutput/storyboard/Working/{dialogue_asset_key}_Audio_Final.wav`。`Working/*/Audio_Final.*` 这类文案只能作为搜索提示，不是当前标准路径。
6. 二期接入现有 `scene-tts/events` / `tts_routes.py` 时，回写 API 入参必须同时携带 `dialogue_asset_key` 和 `dialogue_id`：前者用于稳定路径和 slot 身份，后者用于现有 `update_dialogue_audio_path` 查找 Dialogue；或在二期把两条写回路径收敛成同一套 slot 服务。

## 12. 验收标准

### 12.1 页面初始状态

- 打开页面后，中间角色表和 TTS Prompt 不预先显示。
- 显示空态：`等待 Agent 生成角色表与 TTS Prompt`。

### 12.2 端到端流程

- 用户提交需求后，角色表和 TTS Prompt 必须逐步生成或以可理解的流式状态出现。
- 用户点击角色配置后，角色配置弹窗能打开并套用。
- 用户点击单句 Prompt 配置后，单句配置弹窗能打开并套用。
- 最终能出现声音文件区域的可播放真实音频，并展示相对输出路径。
- 没有真实 provider 配置时，生成和播放按钮必须禁用并显示原因，不允许降级成样例音频或模拟播放态。

### 12.3 角色配置

- 点击角色表 `角色配置` 打开 `角色提示词配置`。
- 切换情景后，关键词字典实时变化。
- 套用后更新角色表。
- 同角色的所有 Prompt 同步更新。

### 12.4 单句 Prompt 配置

- 每句 Dialogue 有 `Prompt 配置`。
- 点击后打开 `单句 Prompt 配置`。
- 套用后只更新当前 Dialogue。
- 不改变角色表。
- 不改变同角色其它 Dialogue。

### 12.5 单句播放

- 每句 Dialogue 有播放按钮。
- 点击后当前卡片高亮播放态。
- 未生成整段音频时也可以播放单句。
- 没有真实 provider 时，单句播放按钮必须禁用并显示原因。

### 12.6 整段声音文件

- 未生成整段音频时，整段播放按钮提示先生成。
- 生成后展示真实音频文件、路径和 waveform。
- 可选择 StoryBoard 槽位并点击确认写回。
- 槽位 select 的 value 必须是结构化 slot key，不允许只靠 label 回写。

### 12.7 配置来源

- provider / model / voice 来自 ModelConfig active TTS 配置。
- UI 不硬编码 `gemini-3.1-flash-tts-preview` 作为业务常量。
- Scenario Guide Registry 是唯一情景字典来源。

## 13. 首版不暴露为可点击功能

以下能力如果没有对应真实后端、真实文件产出和测试，不得作为可点击按钮出现在首版主流程中：

1. Gemini multi-speaker 单次合成 adapter。
2. 不落盘的假单句试听。
3. 展示复杂 provider JSON。
4. 展示多步骤验证状态卡。
5. 支持超过 2 个 Gemini speaker 的直接生成。
6. 长音频自动切片和跨片段拼接。
7. 声音资产库的完整历史管理。
8. 把 provider / model / voice 硬编码为页面常量。

## 14. 后续扩展

后续可扩展：

1. 接入 StoryBoard 生产级 Gemini multi-speaker provider adapter，复用 / 参考 ModelConfig 预览中的 `multiSpeakerVoiceConfig`，并补齐后端生成、缓存、错误处理、回写和测试。
2. 为 Scenario Guide Registry 增加版本号、后台编辑和 A/B 场景配置。
3. 支持 ElevenLabs Text to Dialogue 的 provider adapter。
4. 支持单句真实试听缓存。
5. 支持长文本分段生成和合并。
6. 支持把生成音频发送到数字人、视频生成、口型驱动。

## 15. 文件关系

| 文件 | 说明 |
| --- | --- |
| `docs/TTS_Agent_操作动画演示.html` | 操作和交互效果参考稿，不是生产功能或逐帧验收源 |
| `TTS_Agent_最终交互需求落地.md` | 本需求文档 |
| `TTS_Agent_交互设计_三方案.md` | 早期方案探索稿，仅作背景参考 |
| `ModelConfig/frontend/src/tts/TTSConfigModal.tsx` | Google TTS Scenario Lab 实现，必须消费共享 Scenario Guide Registry |
| `frontend/src/shared/tts/googleTtsScenarioGuide.ts` | Google Scenario Lab 与 TTS Agent 共用的唯一情景字典和 Scenario 元数据来源 |
| `frontend/src/modules/koubo/OCRebuildModule.jsx` | Koubo / Rebuild 中 `Google Scenario Lab` 命名和 Voice Guide 入口参考 |
| `frontend/src/modules/koubo/AnalysisV1/components/AnalysisV1TTSBuilder.jsx` | 可复用的真实音频播放、波形解析、单句试听能力参考 |
