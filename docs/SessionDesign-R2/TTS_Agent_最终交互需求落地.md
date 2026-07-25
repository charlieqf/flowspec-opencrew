# TTS Agent 最终交互需求落地

参考演示文件：`OpenCrew/docs/SessionDesign-R2/TTS_Agent_操作动画演示.html`

## 1. 结论

本需求以当前 HTML 动画为交互基准，并以 2026-06-30 最新确认口径为准：`右侧 Agent 连续对话 + 中间角色表和 TTS 表 + 底部 Asset Audio 声音文件生成`。

TTS Agent 的核心目标不是加载 StoryBoard 中已经存在的对白，也不是让用户手写完整 Gemini TTS payload，而是让用户通过自然语言提示词生成一个可编辑的多角色 Dialogue，并最终生成一个 Asset Audio 文件夹中的音频素材。该素材用于后续拖拽使用，不在 TTS Agent 内直接回写 StoryBoard。

最终推荐形态：

1. 右侧 Agent 接收自然语言需求。
2. 中间工作区从空白开始，生成角色表和 TTS 表。
3. 角色有独立的 `角色配置`。
4. TTS 表中每一行可选择不同角色和 Voice，理论上支持多角色。
5. 配置弹窗参考 Connection 的 Gemini Voice Guide：先选情景，再看该情景的关键词字典。
6. 单句播放只做临时试听，不生成 Asset Audio 素材。
7. 点击生成声音文件后，基于当前 TTS 表生成一个用于一个 Dialogue 的 Asset Audio 音频素材。
8. 不展示 StoryBoard 槽位回写、下游使用状态、Final Video 状态或 Audio_Final 写回状态。

## 2. 用户目标

用户希望在不知道 Gemini TTS 提示词怎么写的情况下，也可以快速完成：

1. 把自然语言需求转成角色表。
2. 把需求转成可编辑的多行 TTS 表。
3. 快速调整某个角色的长期声音人格。
4. 快速调整某一句台词的局部读法。
5. 临时试听单句。
6. 生成一个 Asset Audio 音频素材，后续通过素材拖拽使用。

## 3. 页面布局

### 3.1 顶部

顶部保留：

| 元素 | 行为 |
| --- | --- |
| Reset | 重置演示和工作区状态 |
| 自动播放演示 | 模拟完整 TTS Agent 使用过程 |

顶部不再展示冗余模型状态、Session 说明或上下文路径。模型读取逻辑由 Agent 对话说明，不需要常驻在顶部。

### 3.2 左侧导航

左侧导航用于表达当前入口：

```text
Asset Library
- Images
- Videos
- Audios-Agent

Storyboard
- Scene 03
- Dialogue 02-04
```

当前激活入口为 `Audios-Agent`。

### 3.3 中间工作区

中间工作区包含两个主要区域：

1. `角色表`
2. `TTS 表`
3. `声音文件`

角色表和 TTS 表初始必须为空白，不允许页面一开始从 StoryBoard 加载完整 Dialogue 列表。

初始空态：

```text
等待 Agent 生成角色表与 TTS 表
```

自动播放或真实 Agent 执行时，必须按顺序逐条生成：

1. 角色表出现。
2. 角色 1 出现。
3. 角色 2 出现。
4. TTS 表 Line 01 出现。
5. TTS 表 Line 02 出现。
6. TTS 表 Line 03 出现。

### 3.4 右侧 Agent

右侧 Agent 是唯一的自然语言输入和连续反馈区域。

右侧保留：

| 元素 | 行为 |
| --- | --- |
| Agent 消息流 | 展示识别、生成、配置、生成音频、保存到 Asset Audio 等过程 |
| Composer 草稿 | 用户输入 TTS 需求 |
| 自动播放按钮 | 模拟 Agent 端到端操作 |

中间区域不再放大段输入框，不再复制右侧 Composer。Agent 消息不得出现“已从当前 StoryBoard 恢复 Dialogue”“读取 StoryBoard Dialogue”“写回 Audio_Final”等描述。

## 4. 核心流程

### 4.1 初始输入

用户在右侧 Composer 输入：

```text
我有两个角色，一个小张，一个小白，帮我生成两个人的对话，让我来调整
```

Agent 识别：

| 字段 | 识别结果 |
| --- | --- |
| mode | multi_speaker |
| speaker_count | 2 |
| language | Mandarin |
| source | prompt |
| output | Asset Audio |
| Speaker A | 小张 / 可选 Voice |
| Speaker B | 小白 / 可选 Voice |

### 4.2 逐条生成角色表

角色表字段：

| 字段 | 说明 |
| --- | --- |
| Speaker | 角色名，必须与 TTS 表 speaker label 一致 |
| Voice | TTS voice name |
| Style | 角色长期声音人格 |
| Pace | 角色长期节奏约束 |
| 操作 | `角色配置` / 删除 |

示例：

| Speaker | Voice | Style | Pace |
| --- | --- | --- | --- |
| 小张 | Kore | 自然口播、清晰、可信 | 中速，句尾干净 |
| 小白 | Puck | 活泼、轻快、自然接话 | 略快但不抢话 |

### 4.3 逐条生成 TTS 表

TTS 表是生成声音时的所见即所得输入。每一行都属于同一个 Dialogue 音频素材中的一句台词。

每行包含：

| 元素 | 行为 |
| --- | --- |
| 标题 | `Dialogue 01 / Line 01` |
| Speaker | 可切换到角色表中的任意角色 |
| Voice | 跟随所选角色，可通过角色表调整 |
| `Prompt 配置` | 只配置当前单句 Prompt |
| 播放按钮 | 只生成临时试听，不写入 Asset Audio |
| Prompt 正文 | 当前单句可编辑文本 |

示例：

```text
小张：我们先把这个语音素材的节奏定下来。
小白：好，我想让它更像两个人自然交流。
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

配置弹窗必须参考 Connection 中 Gemini Voice Guide 的使用方式。

### 6.1 弹窗结构

顶部字段：

| 字段 | 说明 |
| --- | --- |
| 当前角色 | 只读，显示当前配置作用到哪个角色 |
| 情景 | 下拉选择，来自 Connection 的 TTS guide scenario |
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

首版必须包含以下情景：

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

### 6.3 情景关键词字典

切换情景时，右侧关键词字典必须实时变化。

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

TTS 表每行必须提供单句播放按钮。

行为：

1. 点击后只生成临时试听音频并播放当前行。
2. 不写入 Asset Audio 文件夹。
3. 当前卡片进入播放高亮态。
4. 不影响其它行。

提示文案示例：

```text
正在播放临时试听
```

## 8. 声音文件

点击生成声音文件后，中间 `声音文件` 区域只展示必要内容。这里生成的是一个用于一个 Dialogue 的 Asset Audio 音频素材，不是 StoryBoard 槽位回写。

| 元素 | 行为 |
| --- | --- |
| 播放按钮 | 播放生成后的 Asset Audio |
| 文件名 | 展示生成的音频文件 |
| 路径 | 展示相对输出路径 |
| waveform | 简单可视化音频波形 |

不展示：

1. 复杂验证状态卡。
2. provider manifest JSON。
3. 多余 execution 状态列表。
4. StoryBoard 槽位选择。
5. 下游使用状态。
6. Audio_Final / Final Video 状态。

## 9. Agent 自动演示流程

自动播放必须按以下顺序执行：

1. 页面进入空白状态。
2. 右侧发送用户需求。
3. Agent 根据提示词识别角色和对话目标。
4. 从 Connection 读取 TTS provider、model、voice。
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
13. 生成声音文件。
14. 展示 Asset Audio 文件名、路径和 waveform。
15. 用户可试听该 Asset Audio，并在素材库中拖拽使用。

## 10. 数据对象

### 10.1 Role

```json
{
  "speaker": "小周",
  "speaker_id": "speaker_b",
  "voice": "Puck",
  "style": ["vocal smile", "自然口播", "商业短视频"],
  "pace": ["略快但清晰", "短停顿", "句尾干净"]
}
```

### 10.2 Dialogue Prompt

```json
{
  "dialogue_id": "Dialogue 02",
  "speaker": "小周",
  "voice": "Puck",
  "scenario": "局部语气切换",
  "prompt": "先平稳、转为强调、收束放慢；提醒感，解释感，确认感；保留 [excitedly]；只朗读当前句：好，我想先听双角色是不是能明显区分。",
  "text": "好，我想先听双角色是不是能明显区分。"
}
```

### 10.3 TTS Generation Request

```json
{
  "provider": "google",
  "model": "gemini-3.1-flash-tts-preview",
  "mode": "multi_speaker",
  "language": "Mandarin",
  "speakers": [
    {
      "speaker": "小林",
      "voice": "Kore"
    },
    {
      "speaker": "小周",
      "voice": "Puck"
    }
  ],
  "prompt_source": "visible_tts_prompt_panel",
  "output_type": "wav",
  "storyboard_target": "dialogue_asset_key"
}
```

### 10.4 Audio Asset

```json
{
  "asset_type": "audio",
  "filename": "double_host_preview.wav",
  "path": "TTS-Agent/generated/double_host_preview.wav",
  "duration_seconds": 8.42,
  "provider": "google",
  "model": "gemini-3.1-flash-tts-preview",
  "source": "TTS-Agent",
  "storyboard_slot": "Scene 03 / Dialogue 02 / Audio Final"
}
```

## 11. 工程规则

### 11.1 Prompt 与正文隔离

最终送给 TTS provider 的 prompt 必须明确区分：

1. 角色 / 导演指令。
2. 情景关键词。
3. 当前实际朗读正文。

不得把旧 prompt 中的历史正文带入当前 Dialogue。

### 11.2 所见即所得

生成声音时，必须以中间 `TTS 表` 当前内容为准。

规则：

1. 用户看到的每行台词，就是本次生成输入。
2. 角色配置后，受影响的 Prompt 必须实时更新。
3. 单句配置后，只更新当前 Prompt。
4. 生成时不得另走一份用户不可见的隐藏 prompt。

### 11.3 多角色支持

多角色不按“最多 2 个 speaker”的方式限制界面，因为 TTS 表每一行都可以选择自己的角色和 Voice。

规则：

1. 角色表可以生成和维护多个角色。
2. TTS 表每行选择一个角色。
3. 生成时按行使用该角色对应的 Voice。
4. 多行最终合成为一个 Asset Audio 音频素材。

### 11.4 Asset Audio 输出

生成音频后必须保存到 Asset Audio 文件夹。

输出路径用于素材库展示和拖拽使用，不写回 StoryBoard 槽位。

## 12. 验收标准

### 12.1 页面初始状态

- 打开页面后，中间角色表和 TTS 表不预先显示。
- 显示空态：`等待 Agent 生成角色表与 TTS 表`。

### 12.2 自动播放

- 点击 `自动播放演示` 后，角色表和 TTS 表必须逐条出现。
- 角色配置弹窗能自动打开并套用。
- 单句 Prompt 配置弹窗能自动打开并套用。
- 最终能出现声音文件区域的可播放音频。

### 12.3 角色配置

- 点击角色表 `角色配置` 打开 `角色提示词配置`。
- 切换情景后，关键词字典实时变化。
- 套用后更新角色表。
- 同角色的所有 Prompt 同步更新。

### 12.4 单句 Prompt 配置

- 每行台词有 `Prompt 配置`。
- 点击后打开 `单句 Prompt 配置`。
- 套用后只更新当前行。
- 不改变角色表。
- 不改变同角色其它行。

### 12.5 单句播放

- 每行台词有播放按钮。
- 点击后当前卡片高亮播放态。
- 未生成最终 Asset Audio 时也可以临时试听单句。
- 单句试听不写入 Asset Audio。

### 12.6 声音文件

- 未生成音频时，播放按钮提示先生成。
- 生成后展示 Asset Audio 文件、路径和 waveform。
- 不展示 StoryBoard 槽位或回写按钮。

## 13. 首版不做

首版不做以下内容：

1. 复杂 provider JSON。
2. 展示多步骤验证状态卡。
3. StoryBoard 槽位回写。
4. 下游使用状态检查。
5. 声音资产库的完整历史管理。

## 14. 后续扩展

后续可扩展：

1. 更丰富的角色生成策略。
2. 将 Connection Voice Guide 的情景字典作为统一数据源。
3. 支持 ElevenLabs Text to Dialogue 的 provider adapter。
4. 支持单句真实试听缓存。
5. 支持长文本分段生成和合并。
6. 支持把生成音频发送到数字人、视频生成、口型驱动。

## 15. 文件关系

| 文件 | 说明 |
| --- | --- |
| `TTS_Agent_操作动画演示.html` | 当前交互动画与视觉基准 |
| `TTS_Agent_最终交互需求落地.md` | 本需求文档 |
| `TTS_Agent_交互设计_三方案.md` | 早期方案探索稿，仅作背景参考 |
