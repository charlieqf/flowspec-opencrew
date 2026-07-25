# Analysis_V1 SRT Rewrite + StoryBoard 工具需求整理

## 1. 背景

当前 Analysis_V1 已经完成以下能力：

1. `02_01_AudioASR`：生成逐句 ASR / SRT。
2. `02_02_VideoSRTFrame`：为每一句对白绑定一帧最清楚的字幕画面。
3. `03_01_TTSBuilderG`：基于对白、画面和场景生成 3 个 Gemini Builder-G 声音候选。

下一步需要在已经稳定的 `srt_id + 时间 + 图片帧` 基础上做两件事：

1. 按 Task 的业务提示词逐句改写 SRT，让对白内容和目标产品、目标人设、视频公式一致。
2. 按改写后的 SRT 和视频公式组织 Shot / Scene 结构，作为后续生成、改写、复拍和 TTS 的统一脚本骨架。

核心原则：

```text
句子数量不能变。
srt_id 不能变。
时间不能变。
图片帧不能变。
每一句改写后的对白仍然和 02_02 选出的图片帧对应。
```

## 2. 工具定位

建议新增两个工具：

```text
04_01_SRTRewrite.py
04_02_SRTStoryBoard.py
```

对应 Step 目录：

```text
S6_04_01_SRTRewrite/
S7_04_02_SRTStoryBoard/
```

完整链路变为：

```text
S1_00_PrepareSessionVariables  <- 00_PrepareSessionVariables.py
S2_01_VideoProbeMetadata       <- 01_VideoProbeMetadata.py
S3_02_01_AudioASR              <- 02_01_AudioASR.py
S4_02_02_VideoSRTFrame         <- 02_02_VideoSRTFrame.py
S5_03_01_TTSBuilderG           <- 03_01_TTSBuilderG.py
S6_04_01_SRTRewrite            <- 04_01_SRTRewrite.py
S7_04_02_SRTStoryBoard         <- 04_02_SRTStoryBoard.py
```

`04_01` 和 `04_02` 都属于 Analysis_V1 主链路，不属于 Backup 工具。

## 3. Prompt Builder 需求

### 3.1 当前界面问题

当前 Prompt Builder 只有：

```text
simple_prompt
final_prompt
```

但 04 阶段需要两个不同用途的 Final Prompt：

1. SRT 改写提示词：指导逐句头对头改写。
2. StoryBoard 提示词：指导按视频公式组织 Shot / Scene。

这两个提示词不能混用，否则会导致：

1. 改写工具夹带分镜组织任务。
2. StoryBoard 工具夹带对白改写任务。
3. 后续复跑时无法知道模型调用具体基于哪一类业务提示词。

### 3.2 新 UI 结构

Prompt Builder 建议改成两个 Tab：

```text
[ SRT Rewrite ] [ StoryBoard ]
```

共享基础字段：

```text
目标视频 / reference_video_path
行业 / industry
人设 / persona
目标受众 / target_audience
视频公式 / video_formula
产品信息 / product_info
约束条件 / constraints
Prompt Model / prompt_model_provider + prompt_model_id
```

SRT Rewrite Tab：

```text
rewrite_simple_prompt
rewrite_final_prompt
```

StoryBoard Tab：

```text
storyboard_simple_prompt
storyboard_final_prompt
```

### 3.3 UI 生成按钮

每个 Tab 都应有独立的生成和保存行为：

```text
生成 SRT 改写复杂提示词
保存 SRT 改写提示词

生成 StoryBoard 复杂提示词
保存 StoryBoard 提示词
```

保存草稿时必须同时保存四个 prompt 字段，避免切换 Tab 后丢失。

### 3.4 兼容旧字段

旧字段仍保留：

```text
simple_prompt
final_prompt
```

兼容规则：

1. 如果新字段为空，UI 可以用旧 `simple_prompt/final_prompt` 初始化 `rewrite_simple_prompt/rewrite_final_prompt`。
2. 新工具必须优先读取新字段。
3. 旧字段只作为迁移兼容，不作为 04 工具的首选输入。

## 4. 数据库字段需求

当前 `openclip_tasks` 和 `openclip_prompt_versions` 只有：

```text
simple_prompt
final_prompt
```

建议新增字段：

```text
rewrite_simple_prompt TEXT
rewrite_final_prompt TEXT
storyboard_simple_prompt TEXT
storyboard_final_prompt TEXT
```

需要同步更新：

1. 后端 schema。
2. repository 创建、更新、版本保存、版本读取。
3. 前端 draft 创建与保存。
4. Prompt version 版本列表和当前版本回填。

如果暂时不做数据库迁移，也可以短期用一个 JSON 字段保存：

```text
prompt_bundle_json
```

但第一版更推荐独立字段，便于 UI 和工具读取。

## 5. 00_PrepareSessionVariables 需求

`00_PrepareSessionVariables.py` 需要把 Task 的业务字段和两个 Final Prompt 写入：

```text
SessionContext/Variables.json
```

建议结构：

```json
{
  "business_context": {
    "industry": "",
    "persona": "",
    "target_audience": "",
    "product_info": "",
    "constraints": "",
    "video_formula": ""
  },
  "rewrite_prompt": {
    "simple_prompt": "",
    "final_prompt": "",
    "source": "openclip_tasks.rewrite_final_prompt"
  },
  "storyboard_prompt": {
    "simple_prompt": "",
    "final_prompt": "",
    "source": "openclip_tasks.storyboard_final_prompt"
  }
}
```

兼容字段：

```json
{
  "simple_prompt": "",
  "final_prompt": ""
}
```

读取优先级：

```text
rewrite_prompt.final_prompt
openclip_tasks.rewrite_final_prompt
openclip_tasks.final_prompt

storyboard_prompt.final_prompt
openclip_tasks.storyboard_final_prompt
openclip_tasks.final_prompt
```

阻塞条件：

1. `rewrite_final_prompt` 缺失时，`04_01_SRTRewrite` 必须 blocked。
2. `storyboard_final_prompt` 缺失时，`04_02_SRTStoryBoard` 必须 blocked。
3. 00 可以允许其中一个缺失，因为用户可能只跑到 04_01 或只准备部分链路。

## 6. 04_01_SRTRewrite 工具需求

### 6.1 工具目标

`04_01_SRTRewrite.py` 负责把原始逐句对白改写成目标产品版本。

它只做逐句改写，不做分镜组织，不做 Shot / Scene，不改时间，不改图片。

### 6.2 输入

必需输入：

```text
SessionContext/Variables.json
SessionOutput/subtitle/final_srt_frame_items.json
SessionOutput/visual/srt_frames/
```

`final_srt_frame_items.json` 每条 item 至少需要：

```json
{
  "srt_id": "srt_0001_01",
  "dialogue": "原对白",
  "image_path": "SessionOutput/visual/srt_frames/srt_0001_01.jpg",
  "start": 0.28,
  "end": 1.68,
  "duration": 1.4
}
```

业务提示词来源：

```text
SessionContext/Variables.json -> rewrite_prompt.final_prompt
```

### 6.3 Prompt 文件要求

所有模型调用提示词必须先写入：

```text
S6_04_01_SRTRewrite/Prompt/00_srt_rewrite_prompt.md
```

调用模型时只能读取这个文件，不允许代码中隐藏拼接 Prompt。

Prompt 文件必须包含：

1. `rewrite_final_prompt` 原文。
2. `business_context`。
3. 原始 `srt_id + start + end + image_path + dialogue` 列表。
4. 输出 JSON schema。
5. 硬性约束：
   - 句子数量必须一致。
   - 每个 `srt_id` 必须保留。
   - 不得合并。
   - 不得拆分。
   - 不得新增。
   - 不得删除。
   - 不得改变时间。
   - 不得改变图片路径。

### 6.4 模型输出要求

模型必须输出严格 JSON：

```json
{
  "items": [
    {
      "srt_id": "srt_0001_01",
      "rewritten_dialogue": "改写后的对白",
      "rewrite_notes": "可选，说明改写点"
    }
  ]
}
```

工具需要把模型输出和原始 item 合并，生成最终 item。

### 6.5 最终输出

最终业务输出：

```text
SessionOutput/subtitle/rewritten_srt_items.json
SessionOutput/subtitle/rewritten_dialogue.srt
```

工具中间输出：

```text
S6_04_01_SRTRewrite/Working/InputFrom_4_final_srt_frame_items.json
S6_04_01_SRTRewrite/Working/InputFrom_0_Variables.json
S6_04_01_SRTRewrite/Prompt/00_srt_rewrite_prompt.md
S6_04_01_SRTRewrite/Output/model_response.json
S6_04_01_SRTRewrite/Output/rewritten_srt_items.json
S6_04_01_SRTRewrite/Report/Result.json
```

最终 JSON schema：

```json
{
  "schema_version": "analysis_v1_rewritten_srt_items_0.1",
  "tool": "04_01_SRTRewrite",
  "source_items_path": "SessionOutput/subtitle/final_srt_frame_items.json",
  "items": [
    {
      "srt_id": "srt_0001_01",
      "dialogue": "改写后的对白",
      "original_dialogue": "原对白",
      "image_path": "SessionOutput/visual/srt_frames/srt_0001_01.jpg",
      "start": 0.28,
      "end": 1.68,
      "duration": 1.4
    }
  ]
}
```

### 6.6 校验规则

工具必须在写最终输出前校验：

1. 输出 item 数量等于输入 item 数量。
2. 输出 `srt_id` 顺序等于输入 `srt_id` 顺序。
3. 每条输出对白非空。
4. 不允许出现新增 `srt_id`。
5. 不允许缺失任何输入 `srt_id`。
6. `start/end/duration/image_path` 必须来自原始 item，不能来自模型输出。
7. 如模型输出不合格，工具必须 blocked 或进入一次 repair 调用。

### 6.7 是否允许 repair

允许最多一次 repair 调用。

repair 仍必须写入 Prompt：

```text
S6_04_01_SRTRewrite/Prompt/01_srt_rewrite_repair_prompt.md
```

repair 只允许修复 JSON 格式、缺项、顺序和空对白，不允许改变约束。

## 7. 04_02_SRTStoryBoard 工具需求

### 7.1 工具目标

`04_02_SRTStoryBoard.py` 负责把改写后的 SRT 组织成 Shot / Scene。

它不改写对白，只做结构化分组。

目标：

```text
按视频公式组织整体结构。
尽量一个 Shot 约 16 秒。
尽量一个 Scene 约 4 秒。
优先保障语义完整和公式完整。
每个 srt_id 必须稳定归属一个 Scene。
每个 Scene 必须归属一个 Shot。
```

### 7.2 输入

必需输入：

```text
SessionContext/Variables.json
SessionOutput/subtitle/rewritten_srt_items.json
SessionOutput/visual/srt_frames/
SessionContext/Video_Metadata.json
```

可选输入：

```text
SessionOutput/subtitle/final_srt_frame_items.json
SessionOutput/visual/srt_frame_map.json
SessionOutput/tts/tts_builder_candidates.json
```

业务提示词来源：

```text
SessionContext/Variables.json -> storyboard_prompt.final_prompt
```

### 7.3 Prompt 文件要求

所有模型调用提示词必须先写入：

```text
S7_04_02_SRTStoryBoard/Prompt/00_srt_storyboard_prompt.md
```

Prompt 文件必须包含：

1. `storyboard_final_prompt` 原文。
2. `business_context`。
3. `video_formula`。
4. 改写后的 `srt_id + start + end + duration + dialogue + image_path` 列表。
5. Shot / Scene 组织目标：
   - Shot 目标约 16 秒。
   - Scene 目标约 4 秒。
   - 但优先语义完整。
   - 不能切断一句话。
   - 不能改变顺序。
6. 输出 JSON schema。

### 7.4 模型输出要求

模型必须输出严格 JSON：

```json
{
  "video_formula": "Hook/Trust/CTA",
  "shots": [
    {
      "shot_id": "shot_001",
      "formula_stage": "Hook",
      "summary": "",
      "srt_ids": ["srt_0001_01"],
      "scenes": [
        {
          "scene_id": "scene_001",
          "summary": "",
          "srt_ids": ["srt_0001_01"]
        }
      ]
    }
  ]
}
```

模型不需要输出时间，时间由工具根据 `srt_ids` 回填，避免模型计算错误。

### 7.5 最终输出

最终业务输出：

```text
SessionOutput/storyboard/srt_storyboard.json
```

工具中间输出：

```text
S7_04_02_SRTStoryBoard/Working/InputFrom_6_rewritten_srt_items.json
S7_04_02_SRTStoryBoard/Working/InputFrom_0_Variables.json
S7_04_02_SRTStoryBoard/Prompt/00_srt_storyboard_prompt.md
S7_04_02_SRTStoryBoard/Output/model_response.json
S7_04_02_SRTStoryBoard/Output/srt_storyboard.json
S7_04_02_SRTStoryBoard/Report/Result.json
```

最终 JSON schema：

```json
{
  "schema_version": "analysis_v1_srt_storyboard_0.1",
  "tool": "04_02_SRTStoryBoard",
  "video_formula": "Hook/Trust/CTA",
  "source_items_path": "SessionOutput/subtitle/rewritten_srt_items.json",
  "shots": [
    {
      "shot_id": "shot_001",
      "formula_stage": "Hook",
      "start": 0.28,
      "end": 16.28,
      "duration": 16.0,
      "summary": "",
      "srt_ids": ["srt_0001_01", "srt_0001_02"],
      "key_frame_paths": [
        "SessionOutput/visual/srt_frames/srt_0001_01.jpg"
      ],
      "scenes": [
        {
          "scene_id": "scene_001",
          "start": 0.28,
          "end": 4.28,
          "duration": 4.0,
          "summary": "",
          "srt_ids": ["srt_0001_01"],
          "dialogue_items": [
            {
              "srt_id": "srt_0001_01",
              "dialogue": "改写后的第一句对白",
              "start": 0.28,
              "end": 2.28,
              "duration": 2.0,
              "image_path": "SessionOutput/visual/srt_frames/srt_0001_01.jpg"
            }
          ],
          "key_frame_paths": [
            "SessionOutput/visual/srt_frames/srt_0001_01.jpg"
          ]
        }
      ]
    }
  ]
}
```

### 7.6 校验规则

工具必须校验：

1. 每个输入 `srt_id` 必须出现且只出现一次。
2. `srt_id` 顺序不能倒置。
3. 每个 Scene 的 `srt_ids` 必须连续。
4. 每个 Shot 的 Scene 必须连续。
5. Scene 必须归属 Shot。
6. Shot 时间由第一个和最后一个 `srt_id` 回填。
7. Scene 时间由第一个和最后一个 `srt_id` 回填。
8. Shot 目标约 16 秒，但可因语义完整偏离。
9. Scene 目标约 4 秒，但可因语义完整偏离。
10. 如果模型漏掉、重复或乱序 `srt_id`，必须 blocked 或进入一次 repair。

### 7.7 repair 规则

允许最多一次 repair 调用。

repair Prompt：

```text
S7_04_02_SRTStoryBoard/Prompt/01_srt_storyboard_repair_prompt.md
```

repair 只允许修复：

1. JSON 格式。
2. 漏掉的 `srt_id`。
3. 重复的 `srt_id`。
4. 非连续分组。
5. 空 summary。

不允许 repair 改写对白。

## 8. 模型选择与 API Key

04 阶段需要使用文本大模型。

建议读取：

```text
SessionContext/Variables.json -> prompt_model_provider
SessionContext/Variables.json -> prompt_model_id
```

如果未来 00 写入更明确的 LLM 配置，可以改为：

```json
{
  "default_text_model_config": {
    "provider": "",
    "model": "",
    "api_key_ref": "",
    "has_api_key": true
  }
}
```

API key 规则：

1. 不写入 `Variables.json`。
2. 不写入任何 Result / Output。
3. 工具运行时从数据库读取到内存。
4. 和 `02_01_AudioASR`、`03_01_TTSBuilderG` 的方式保持一致。

## 9. Prompt 透明性规则

两个工具都必须遵守：

```text
所有模型调用 Prompt 先写入工具 Prompt 目录。
模型调用时只读取 Prompt 文件。
代码中不允许隐藏拼接额外业务指令。
Prompt 文件要足够完整，可以被人工审阅和复跑。
```

对应目录：

```text
S6_04_01_SRTRewrite/Prompt/
S7_04_02_SRTStoryBoard/Prompt/
```

## 10. 与图片帧的关系

04 阶段不重新选帧。

图片来源固定为：

```text
SessionOutput/visual/srt_frames/
```

关联方式固定为：

```text
srt_id -> image_path
```

`04_01` 改写对白后，仍然保持原 `image_path`。

`04_02` 组织 Scene / Shot 时，只引用每个 Scene / Shot 包含的 `srt_id` 对应图片。

## 11. 与 TTS 的关系

`03_01_TTSBuilderG` 现在基于旧对白生成声音候选。

04 阶段引入后，有两种可能链路：

### 11.1 当前推荐链路

```text
02_02 -> 03_01 -> 04_01 -> 04_02
```

含义：

1. 先用原片声音风格选 TTS 候选。
2. 再改写脚本。
3. 再组织 StoryBoard。

### 11.2 后续可选链路

```text
02_02 -> 04_01 -> 04_02 -> 03_01
```

含义：

1. 先改写脚本。
2. 再按新脚本组织 StoryBoard。
3. 再基于新脚本生成 TTS 候选。

第一版不强制改变 `03_01` 位置，但如果后续 TTS 试听必须读改写后文本，则应支持 `03_01` 可选读取：

```text
SessionOutput/subtitle/rewritten_srt_items.json
```

## 12. 输出边界

04 工具最终只输出机器可消费 JSON / SRT。

不生成 HTML。

人工 review 页面应由独立 UI 或独立报告生成器读取：

```text
rewritten_srt_items.json
srt_storyboard.json
srt_frames/
```

## 13. 阻塞条件

`04_01_SRTRewrite` blocked 条件：

1. `Variables.json` 缺失。
2. `rewrite_prompt.final_prompt` 缺失。
3. `final_srt_frame_items.json` 缺失。
4. 输入 items 为空。
5. 模型配置缺失。
6. API key 缺失。
7. 模型输出句数不一致且 repair 失败。
8. 模型输出 `srt_id` 不一致且 repair 失败。

`04_02_SRTStoryBoard` blocked 条件：

1. `Variables.json` 缺失。
2. `storyboard_prompt.final_prompt` 缺失。
3. `rewritten_srt_items.json` 缺失。
4. 输入 items 为空。
5. 模型配置缺失。
6. API key 缺失。
7. 模型输出漏掉或重复 `srt_id` 且 repair 失败。
8. Scene / Shot 非连续且 repair 失败。

## 14. 验收标准

### 14.1 04_01 验收

1. 成功生成 `SessionOutput/subtitle/rewritten_srt_items.json`。
2. 成功生成 `SessionOutput/subtitle/rewritten_dialogue.srt`。
3. 输出 item 数量和输入完全一致。
4. 输出 `srt_id` 顺序和输入完全一致。
5. 每条 item 保留原始 `start/end/duration/image_path`。
6. 每条 item 有新 `dialogue` 和 `original_dialogue`。
7. Prompt 文件可审阅。
8. Result 中不包含 API key。

### 14.2 04_02 验收

1. 成功生成 `SessionOutput/storyboard/srt_storyboard.json`。
2. 每个 `srt_id` 出现且只出现一次。
3. Scene 全部归属 Shot。
4. Shot / Scene 时间由真实 SRT 时间回填。
5. Shot 平均接近 16 秒，但允许为语义完整偏离。
6. Scene 平均接近 4 秒，但允许为语义完整偏离。
7. 每个 Shot 有 `formula_stage`。
8. Prompt 文件可审阅。
9. Result 中不包含 API key。

## 15. 待确认问题

1. 是否立即做数据库迁移，新增四个 prompt 字段？
2. `rewrite_final_prompt` 和 `storyboard_final_prompt` 是否都由同一个 Prompt Model 生成？
3. `04_01` 是否需要强制真实模型调用，还是允许 `--dry-run` 只生成 Prompt？
4. `04_02` 是否需要生成 Shot / Scene 的标题字段，例如 `shot_title`、`scene_title`？
5. `03_01_TTSBuilderG` 后续是否要改为读取改写后的 SRT？
6. UI 是否需要在 Dialogue View 中并排展示原对白和改写对白？
7. UI 是否需要在同一界面预览 StoryBoard 的 Shot / Scene 分组？
