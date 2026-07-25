# Analysis_V1 TTS Builder 工具需求整理

## 1. 工具目标

TTS Builder 的目标不是直接生成最终成片音频，而是为后续 TTS 生成工具自动推荐 3 个可试听、可选择、可复用的候选声音方案。

每个候选方案必须同时包含：

1. 候选声音名称 / provider / model / voice。
2. 针对该声音的声音提示词。
3. 样本试听音频。
4. 推荐 Tempo / speed 调整值。
5. 评分与推荐理由。

这个工具要解决的问题是：用户不再手动盲选 Builder-G 的声音和提示词，而是由系统根据参考对白、参考声音、每句对应画面和整体视觉场景，先给出 3 个最可能合适的 Builder-G 候选。

## 2. 建议工具定位

如果放入当前 Analysis_V1 主链路，建议作为 02_02 之后的新工具：

```text
03_01_TTSBuilderG.py
S5_03_01_TTSBuilderG/
```

它消费 02_02 的最终逐句对白和图片绑定结果：

```text
SessionOutput/subtitle/final_srt_frame_items.json
SessionOutput/visual/srt_frames/
SessionOutput/Audio_Reference.wav
```

它不修改 02_02 的字幕结果，也不改写 SRT。

## 3. 参考现有能力

现有可参考工具和逻辑：

1. `OpenCrew/ToolLibrary/Rebuild_V1/03_01_ShotPlan_TTSReferenceAudioExtract.py`
   - 负责定位参考音频。
   - 可复用“从 source_package / analysis_workspace 找 reference_audio”的思路。

2. `OpenCrew/ToolLibrary/Rebuild_V1/03_02_ShotPlan_TTSVoiceRecommend.py`
   - 负责根据参考音频和候选 voice profile 排名。
   - 可复用音色匹配评分思路。

3. `OpenCrew/ToolLibrary/Rebuild_V1/03_02_ShotPlan_GTTSVoiceBuilder.py`
   - 负责 Gemini TTS 试听生成、候选评分、duration 拟合。
   - 可复用 `audio_features`、`score_candidate`、`fit_audio_to_duration`、`atempo_chain`。

4. `docs/opencrew-tts-voice-match-algorithm.md`
   - 可复用 gender / Mandarin / timbre / pitch / brightness / energy / speaking rate 的排序原则。

第一版只实现 Builder-G / Gemini TTS，不实现 Builder-Q / Qwen 分支。Qwen 相关工具只作为历史参考，不进入本工具范围。

## 4. 输入要求

### 4.1 必需输入

```text
SessionContext/Variables.json
SessionContext/Video_Metadata.json
SessionOutput/subtitle/final_srt_frame_items.json
SessionOutput/visual/srt_frames/
SessionOutput/Audio_Reference.wav
```

`final_srt_frame_items.json` 中每条 item 至少需要：

```json
{
  "srt_id": "srt_0001_01",
  "dialogue": "给我老公买这个润喉糖啊",
  "image_path": "SessionOutput/visual/srt_frames/srt_0001_01.jpg",
  "start": 0.28,
  "end": 1.68,
  "duration": 1.4
}
```

`SessionContext/Variables.json` 必须由 `00_PrepareSessionVariables.py` 写入 Builder-G 的 Gemini 模型选择信息：

```json
{
  "default_tts_config": {
    "kind": "tts",
    "provider": "google",
    "model": "gemini-3.1-flash-tts-preview",
    "builder_g_default_model": "gemini-3.1-flash-tts-preview",
    "scene_profile_model": "gemini-3.1-flash-image-preview",
    "scene_profile_default_model": "gemini-3.1-flash-image-preview",
    "api_key_ref": "google_gemini_tts_key",
    "has_api_key": true,
    "source": "postgres:tool_media_provider_configs"
  },
  "gemini_builder_g_config": {
    "provider": "google",
    "selected_tts_model": "gemini-3.1-flash-tts-preview",
    "default_tts_model": "gemini-3.1-flash-tts-preview",
    "selected_scene_profile_model": "gemini-3.1-flash-image-preview",
    "default_scene_profile_model": "gemini-3.1-flash-image-preview"
  }
}
```

这里不能写入 API key，只能写 `api_key_ref` 和 `has_api_key`。`03_01_TTSBuilderG.py` 运行时从 `Variables.json` 选择模型，并参考 ASR 的方式从数据库读取 API key 到内存中完成 Gemini 调用。

### 4.2 可选输入

```text
SessionOutput/subtitle/calibrated_srt_items.json
SessionOutput/visual/srt_frame_map.json
OpenCrew/ModelConfig/tts_voice_previews/
数据库中的 TTS provider config
```

可选输入用于增强评分和 provider 可用性检查，但最终业务输入应优先依赖简化后的 `final_srt_frame_items.json`。

## 5. 输出要求

最终业务输出建议只保留一份 JSON：

```text
SessionOutput/tts/tts_builder_candidates.json
```

候选试听音频放在：

```text
SessionOutput/tts/tts_builder_candidate_001.wav
SessionOutput/tts/tts_builder_candidate_002.wav
SessionOutput/tts/tts_builder_candidate_003.wav
```

`SessionOutput/tts/` 必须尽量平，不再创建 `samples/`、`reference/`、`raw/` 等子目录。所有最终要给 UI 和后续工具消费的文件都直接放在这一层。

工具自己的中间产物和审计文件放在：

```text
S5_03_01_TTSBuilderG/Working/
S5_03_01_TTSBuilderG/Output/
S5_03_01_TTSBuilderG/Prompt/
S5_03_01_TTSBuilderG/Report/Result.json
```

所有会调用模型的提示词必须先写入 `S5_03_01_TTSBuilderG/Prompt/`，最终调用模型时只能从这些 prompt 文件读取内容，不允许在代码里临时拼接后直接调用。这样可以保证每一次模型调用的提示词可审计、可复跑、可人工修改。

最终 JSON 建议结构：

```json
{
  "schema_version": "analysis_v1_tts_builder_candidates_0.1",
  "sample_policy": {
    "selected_duration": 16,
    "tested_durations": [4, 8, 16],
    "selected_range": {"start": 0.28, "end": 16.28},
    "reason": "16s gives the most stable voice and tempo estimate for expressive product-dialogue narration"
  },
  "scene_profile": {
    "language": "zh",
    "speaker_gender": "female",
    "scene_type": "home_lifestyle_product_recommendation",
    "delivery_style": "natural close-mic short-video product sharing",
    "emotion": "friendly, slightly lively, persuasive",
    "pace": "slightly fast",
    "visual_basis": ["indoor home/kitchen", "host holding product", "subtitle-style spoken review"]
  },
  "candidates": [
    {
      "rank": 1,
      "candidate_id": "tts_001",
      "provider": "google",
      "model": "gemini-3.1-flash-tts-preview",
      "voice": "Aoede",
      "voice_label": "Aoede",
      "prompt": "普通话年轻女性，居家生活短视频口播，近距离自然收音，语气亲切真实，像在给家人/朋友安利产品；语速略快但清晰，不要播音腔，不要过度甜。",
      "prompt_path": "S5_03_01_TTSBuilderG/Prompt/tts_builder_candidate_001_prompt.txt",
      "sample_audio_path": "SessionOutput/tts/tts_builder_candidate_001.wav",
      "tempo": 1.04,
      "tempo_source": "measured_after_raw_tts_generation",
      "raw_duration": 16.64,
      "target_duration": 16.0,
      "fit_duration": 16.0,
      "score": 0.91,
      "reason": "声线、音高、节奏和画面中的居家产品口播场景最接近"
    }
  ]
}
```

## 6. 抽样时长策略：4 秒、8 秒、16 秒

工具需要显式比较或至少记录三种抽样长度对效果和运行时长的影响。

### 6.1 4 秒样本

优点：

1. 运行最快。
2. TTS 生成成本最低。
3. 适合快速预览 UI 交互。

缺点：

1. 对音色判断不稳定，容易被一句话里的局部情绪误导。
2. Tempo 估计不稳，尤其口播里有停顿、语气词、产品名时。
3. 很难判断完整场景风格，只能判断局部声线。

适用场景：

1. 首次粗筛。
2. provider 很慢或用户只想快速看候选。
3. 不作为最终推荐的唯一依据。

结论：

```text
4s = fast preview，不建议作为最终自动选择依据。
```

### 6.2 8 秒样本

优点：

1. 音色和节奏比 4 秒稳定。
2. 运行时间仍可接受。
3. 通常能覆盖 2-4 句短口播，对生活短视频已经有一定代表性。

缺点：

1. 如果场景前 8 秒刚好是过渡或单一产品展示，情绪判断仍可能偏。
2. Tempo 估计比 16 秒略不稳。

适用场景：

1. 默认快速模式。
2. 多 provider 多 voice 大范围搜索。
3. 先筛出候选 voice，再用 16 秒精排。

结论：

```text
8s = 推荐作为第一轮候选搜索长度。
```

### 6.3 16 秒样本

优点：

1. 对音色、节奏、停顿、情绪、语速最稳定。
2. 更容易覆盖完整的表达方式和视觉场景。
3. Tempo 估计更可靠，适合作为最终三候选的试听长度。
4. 和当前 Builder-G / Builder-Q 的使用习惯一致。

缺点：

1. 每个候选 TTS 生成耗时明显增加。
2. 多 voice、多 prompt、多 provider 时总运行时长会膨胀。
3. 如果原片 16 秒内包含多个情绪段，单一提示词可能需要兼容多风格。

适用场景：

1. 最终三候选试听。
2. 需要较准确 Tempo。
3. 需要把声音提示词和画面场景结合。

结论：

```text
16s = 推荐作为最终候选输出长度。
```

### 6.4 推荐默认流程

推荐采用“两段式”：

1. 用 8 秒做较快的 voice 粗筛，筛出 top 6。
2. 用 16 秒对 top 6 做 Builder-G 提示词生成、试听生成、Tempo 拟合和最终排序。
3. 最终只输出 top 3。

如果用户指定 `--sample-duration 4|8|16`，则尊重用户指定。

如果工具需要快速模式：

```text
--mode fast      -> 4s 或 8s，只适合预览
--mode balanced  -> 8s 粗筛 + 16s 精排
--mode quality   -> 16s 全量候选
```

## 7. 抽样片段选择策略

不能简单固定从 0 秒开始截取。应该选择“最能代表声音和场景”的片段。

优先规则：

1. 有连续对白。
2. OCR/ASR 校准置信度较高。
3. 避免开头无效口头禅、片头、强背景音乐、静音。
4. 覆盖 2-6 句完整对白。
5. 对应图片能明确看出场景类型，例如居家、产品展示、口播、探店、讲解、剧情等。
6. 尽量包含说话人的自然语气，而不是只包含产品名或短句。

如果视频总时长不足 16 秒：

1. 使用完整可用对白段。
2. 在 JSON 中记录 `selected_duration < requested_duration`。
3. Tempo target 使用真实片段时长。

## 8. 场景识别与声音提示词生成

声音提示词必须参考两类证据：

1. 对白内容：从 `dialogue` 判断语气、身份、产品属性、情绪强度。
2. 视觉场景：从每句图片判断画面环境和表达方式。

### 8.1 场景 profile 需要回答的问题

工具需要为样本片段生成一个 `scene_profile`：

1. 这是口播、剧情、采访、讲解、带货、评测、探店、教程还是广告？
2. 说话人像什么身份：年轻女性、年轻男性、妈妈、医生、老师、店员、主持人、用户体验者？
3. 场景在哪里：居家、厨房、办公室、户外、门店、诊室、直播间？
4. 情绪是什么：自然、兴奋、着急、温柔、权威、吐槽、认真、安抚？
5. 语速应该怎样：偏快、正常、慢、紧凑、有停顿？
6. 声音要避免什么：播音腔、广告腔、过度甜、方言、外语口音、太老成、太儿童感？

### 8.2 是否需要大模型

这个工具可以分两层：

1. 纯本地 / 规则层：基于对白关键词、音频特征、候选 voice profile 做初筛和 Tempo。
2. 可选多模态模型层：基于抽样图片 + 对白总结 scene_profile 和声音提示词。

建议：

```text
声音匹配和 Tempo 不依赖大模型。
场景理解和声音提示词生成可以使用小成本多模态/文本模型，但必须可降级到规则模板。
```

理由：

1. 音色和 Tempo 是可测量的，不需要大模型判断。
2. “这是什么场景、应该用什么口吻”更适合模型辅助。
3. 如果没有模型，规则模板也能产出可用候选，但风格会更保守。

### 8.3 Prompt 透明性合同

凡是调用模型的环节，都必须先把提示词落盘到 `Prompt/`：

```text
S5_03_01_TTSBuilderG/Prompt/
  00_scene_profile_prompt.md
  00_scene_profile_request.json
  01_candidate_voice_prompt_build.md
  01_candidate_voice_request.json
  tts_builder_candidate_001_prompt.txt
  tts_builder_candidate_002_prompt.txt
  tts_builder_candidate_003_prompt.txt
```

调用规则：

1. scene profile 如果调用多模态模型，必须从 `00_scene_profile_prompt.md` 读取提示词。
2. 候选声音提示词如果调用文本模型生成，必须从 `01_candidate_voice_prompt_build.md` 读取提示词。
3. Builder-G / Gemini TTS 最终生成 sample audio 时，必须从对应的 `tts_builder_candidate_XXX_prompt.txt` 读取声音提示词。
4. `Result.json` 和最终 `tts_builder_candidates.json` 必须记录每个候选实际使用的 `prompt_path`。
5. 如果用户手动修改 Prompt 文件后 rerun，工具应使用修改后的文件内容，而不是重新覆盖后调用，除非显式 `--force-regenerate-prompts`。

### 8.4 scene_profile 判断方法

第一版建议使用“本地筛帧 + 单次多模态模型判断 + 规则兜底”的方式。

硬性约束：

```text
Scene_Profile 只允许一次模型调用。
该调用只能输入一个 Prompt 文件和一个 contact sheet。
Prompt 必须先写入 S5_03_01_TTSBuilderG/Prompt/。
模型调用时必须读取该 Prompt 文件的原文，做到所见即所得。
代码中不得在调用前额外拼接隐藏提示词、隐藏约束或隐藏上下文。
```

核心原则：

```text
不要把所有句子图片都发给模型。
先在本地选出少量代表帧，再把代表帧拼成 contact sheet，连同对白摘要一起交给多模态模型判断 scene_profile。
```

推荐流程：

1. 从 `final_srt_frame_items.json` 读取每句话的一帧图片、对白和时间。
2. 先确定 TTS 样本时间窗，例如最终 16 秒样本区间。
3. 只在这个样本区间内选图；如果样本区间信息不足，再从全片补充代表帧。
4. 本地选出最多 6 张代表帧。
5. 把 6 张图拼成一张 contact sheet，每格标注 `srt_id`、时间、简短对白。
6. 将 contact sheet 和 `Prompt/00_scene_profile_prompt.md` 发送给多模态模型。
7. 模型只输出结构化 `scene_profile` 和 prompt direction，不直接生成 TTS 音频。
8. 工具根据 `scene_profile` 生成 Builder-G 候选 prompt，并先写入 `Prompt/`。

如果不允许调用大模型：

1. 使用对白关键词 + 本地图片文件名/时间分布 + 简单规则模板生成 conservative scene_profile。
2. 标记 `scene_profile.source = "rule_fallback"`。
3. 候选 prompt 采用保守模板，例如“普通话、自然口播、清晰、不要播音腔”。

### 8.5 候选图片选择方法

这里的“候选图片”不是 02_02 内部的候选帧，而是 `final_srt_frame_items.json` 中每句话已经选好的那一帧。TTS Builder 不再重新 OCR/抽帧，只从这些最终句子帧中挑代表帧。

图片选择分三步：

#### 第一步：时间窗过滤

优先选择 TTS 样本时间窗内的帧：

```text
sample_start <= frame_time <= sample_end
```

如果 `final_srt_frame_items.json` 没有 `frame_time`，则用句子的 `start/end` 判断是否落在样本时间窗内。

#### 第二步：本地打分

每个句子帧计算一个代表性分数：

```text
score =
  0.30 * timeline_coverage_score
  0.25 * dialogue_information_score
  0.20 * visual_diversity_score
  0.15 * duration_score
  0.10 * image_quality_score
```

各项含义：

1. `timeline_coverage_score`：保证开头、中间、结尾都有代表，不只选连续几张。
2. `dialogue_information_score`：对白越能说明场景、人物、产品和情绪，分数越高；过滤“啊、嗯、然后、就是”等信息量低的句子。
3. `visual_diversity_score`：用 perceptual hash / color histogram / SSIM 去重，避免 6 张都是几乎一样的脸部近景。
4. `duration_score`：句子时长太短通常信息不足，适当降权。
5. `image_quality_score`：图片尺寸、清晰度、是否可读，避免模糊帧。

#### 第三步：分层选帧

最终最多选 6 张：

1. 时间窗开头代表 1 张。
2. 时间窗中段代表 1-2 张。
3. 时间窗结尾代表 1 张。
4. 视觉差异最大的补充 1-2 张。
5. 如果出现产品特写、人物正脸、环境全景，优先保留这些视觉类型各 1 张。

如果样本区间内不足 3 张：

1. 从全片按时间均匀补帧。
2. 优先补充视觉差异大的帧。
3. 在 `scene_profile.evidence_scope` 中标记 `sample_window_plus_global_frames`。

### 8.6 多模态模型输入和输出

多模态模型输入不应该是散乱图片列表，而应该是明确的审计材料：

```text
Prompt:
  S5_03_01_TTSBuilderG/Prompt/00_scene_profile_prompt.md

Images:
  S5_03_01_TTSBuilderG/Working/scene_profile_contact_sheet.jpg
```

`selected_dialogue`、`selected_srt`、候选帧说明、输出 JSON schema 都必须写进 `00_scene_profile_prompt.md` 文件本身。模型调用代码只允许读取该文件内容，不允许在代码里另行追加这些文本。

模型输出必须是结构化 JSON：

```json
{
  "scene_type": "home_lifestyle_product_recommendation",
  "speaker_profile": "young adult female lifestyle sharer",
  "environment": "indoor home/kitchen",
  "emotion": "friendly, slightly lively, persuasive",
  "delivery_style": "natural close-mic short-video product sharing",
  "pace": "slightly fast",
  "avoid": ["broadcast tone", "overly sweet", "dialect accent", "foreign accent"],
  "visual_evidence": ["host holding product", "indoor home background", "subtitle product review"],
  "dialogue_evidence": ["mentions husband buying lozenges", "product recommendation wording"]
}
```

`scene_profile` 的输出要进入最终 JSON，但模型原始 response 只放在工具 `Output/` 审计文件中，不塞进最终业务 JSON。

### 8.7 Scene_Profile Prompt 文件格式

`Prompt/00_scene_profile_prompt.md` 必须是完整、可人工审阅、可独立复用的提示词文件。建议包含：

```text
# Scene Profile Prompt

## Task
请根据 contact sheet 和下方对白信息，判断这个 TTS 样本对应的声音场景。

## Input Images
你会看到一张 contact sheet。每格图片上已经标注 srt_id、时间和对白摘要。

## Selected Dialogue
...

## Selected SRT
...

## Required Output JSON
{
  "scene_type": "",
  "speaker_profile": "",
  "environment": "",
  "emotion": "",
  "delivery_style": "",
  "pace": "",
  "avoid": [],
  "visual_evidence": [],
  "dialogue_evidence": []
}

## Rules
只输出 JSON，不要输出解释。
不要生成 TTS。
不要推荐具体 voice。
如果证据不足，用 conservative_unknown，并在 evidence 中说明不足。
```

调用审计要求：

1. `Prompt/00_scene_profile_prompt.md` 是唯一文本输入。
2. `Working/scene_profile_contact_sheet.jpg` 是唯一图片输入。
3. `Output/scene_profile_response.json` 保存模型原始结构化输出。
4. `Report/Result.json` 记录 `scene_profile_prompt_path`、`contact_sheet_path`、模型名称和调用时间。
5. 如果 `00_scene_profile_prompt.md` 已存在且非空，默认复用；只有显式 `--force-regenerate-prompts` 才能覆盖。

## 9. 候选声音推荐逻辑

推荐顺序：

1. 先根据参考音频判断 speaker gender / pitch / timbre。
2. 过滤明显不合适的 voice，例如性别不匹配、外语/方言不适合、音色过老或过儿童。
3. 对候选 voice 生成短样本。
4. 抽取候选样本音频特征。
5. 计算综合评分。
6. 对 top voice 生成不同提示词变体。
7. 输出最终 top 3。

评分维度：

```text
音色相似度 / timbre
音高相似度 / pitch
普通话适配度 / Mandarin fit
清晰度 / clarity
情绪匹配 / expressiveness
语速匹配 / speaking rate
视觉场景匹配 / scene fit
时长拟合 / duration fit
```

已有算法中 speaking rate 权重较低，这个工具可以保留这个原则：语速用于 Tempo 调整和 close ranking，不应该覆盖音色和场景匹配。

## 10. Tempo 需求

Tempo 的目标是让候选试听和后续最终 TTS 更接近原视频节奏。

Tempo 不能只靠本地估算。原因是本地根据字符数、原片语速或参考音频估出来的时长，和 Builder-G 最终生成音频的真实时长可能差很多。

因此第一版规则是：

```text
本地 Tempo 只做粗略 hint，不作为最终推荐值。
最终 Tempo 必须在 Builder-G 真实生成 raw sample 后，用实际音频时长重新计算。
```

最终计算方式：

```text
tempo = raw_tts_duration / target_reference_duration
```

含义：

1. `tempo > 1.0`：TTS 原始音频偏长，需要加速。
2. `tempo < 1.0`：TTS 原始音频偏短，需要放慢或补停顿。
3. 建议输出范围默认限制在 `0.85 - 1.20`，极端情况需要 `needs_review=true`。

### 10.1 推荐处理流程

1. 先选定参考片段，得到 `target_reference_duration`，例如 16.0 秒。
2. 基于对白和画面生成候选 Prompt，并写入 `Prompt/`。
3. 调用 Builder-G 生成 raw sample。
4. 用 ffprobe / 本地音频探测读取 raw sample 的真实时长。
5. 用真实时长计算 `tempo`。
6. 如果 raw 时长偏差不大，直接生成 fitted sample。
7. 如果 raw 时长偏差过大，先尝试通过 Prompt 修正重生成，再做音频级拟合。

### 10.2 Prompt 修正优先于强行 atempo

如果真实 TTS 时长明显偏离目标，不应第一步就强行拉伸音频。

建议阈值：

```text
0.92 <= tempo <= 1.10  -> 可直接 atempo 微调
1.10 < tempo <= 1.20   -> Prompt 增加“语速更快、停顿更短”，重生成一次；仍偏长再 atempo
0.85 <= tempo < 0.92   -> Prompt 增加“语速稍慢、保留自然停顿”，重生成一次；仍偏短再 atempo/pad
tempo > 1.20 或 tempo < 0.85 -> 标记 needs_review=true，允许生成 fitted sample，但不能自动认为高质量
```

原因：

1. `atempo` 可以修时长，但不能修复模型本身的停顿、表达和语气问题。
2. 过度加速会让声音变紧、变假。
3. 过度放慢或补静音会破坏口播自然度。
4. Prompt 先约束语速，再用小幅 atempo 精确对齐，是更稳的方案。

最终候选里必须同时记录：

```json
{
  "tempo": 1.04,
  "tempo_source": "measured_after_raw_tts_generation",
  "raw_duration": 16.64,
  "target_duration": 16.0,
  "fit_duration": 16.0,
  "needs_review": false
}
```

工具可以生成 fitted sample。建议最终 `sample_audio_path` 指向 fitted sample，因为用户需要听到“按目标时长拟合后”的效果。raw sample 不进入 `SessionOutput/tts/`，只放在 `S5_03_01_TTSBuilderG/Working/` 或 `S5_03_01_TTSBuilderG/Output/` 作为审计。

### 10.3 候选 Tempo 不等于最终全片 Tempo

TTS Builder 输出的 Tempo 是基于 4/8/16 秒样本得到的候选推荐值。后续如果生成完整视频/shot 的 TTS，仍然必须：

1. 先生成完整 raw TTS。
2. 再测量完整 raw TTS 时长。
3. 再根据完整目标 SRT 时长重新计算最终 Tempo。
4. 最后输出 locked TTS。

也就是说：

```text
TTS Builder tempo = 候选推荐 / 初始默认值
Final TTS tempo   = 完整 TTS 生成后重新测量得到的最终值
```

不能把 TTS Builder 的 16 秒样本 Tempo 当成全片绝对 Tempo。

## 11. Prompt 生成要求

提示词不是越长越好，需要可控、可迁移、能用于后续整段 TTS。

每个候选的 prompt 至少包含：

1. 语言：普通话 / 中文。
2. 性别和年龄感：年轻女性、成熟男性等。
3. 场景：居家产品分享、专业讲解、剧情对白等。
4. 收音感：近距离、自然、清晰。
5. 情绪：亲切、轻快、权威、安抚、兴奋等。
6. 节奏：略快、自然、短停顿、不要拖沓。
7. 负面约束：不要播音腔、不要方言、不要外语口音、不要过度表演。

示例：

```text
普通话年轻女性，居家生活短视频口播，近距离自然收音。
语气亲切真实，像在给家人/朋友安利产品；语速略快但清晰，句尾自然。
不要播音腔，不要过度甜，不要方言口音，不要外语口音。
```

不同候选不应该只换 voice 名称，还应该形成有意义的风格差异：

1. 候选 1：最贴近原声。
2. 候选 2：更自然、更像真实自拍视频。
3. 候选 3：更清楚但仍保持日常说话感。

### 11.1 每个 voice 的基础 Prompt 如何生成

Round 1 的基础 Prompt 不应该由模型临时自由生成，也不应该只写“请朗读正文”。它由工具本地根据结构化信息组装，然后写入 `Prompt/round1_voice_XXX_prompt.txt`。

基础 Prompt 的输入：

1. `scene_profile`：来自唯一一次多模态模型调用。
2. `scene_profile.voice_prompt_guidance`：由同一次多模态模型基于画面和对白给出的 TTS 声音指导，是本地拼接 Voice Prompt 的优先来源。
3. `voice_profile`：Gemini voice 的已知标签或工具内置说明，例如 `Aoede` 偏自然日常、`Kore` 偏清楚平稳等。
4. `reference_audio_profile`：参考音频的性别、音高、语速、能量、表达强度。
5. `sample_text`：8 秒样本窗口内要朗读的正文。
6. `global_negative_constraints`：统一负面约束，例如不要播音腔、不要广告腔、不要直播叫卖感、不要添加内容。

基础 Prompt 采用固定模板：

```text
请用 {language} 朗读下面正文，只朗读正文，不要读出任何说明。

声音方向：
- 说话人：{speaker_profile}
- 场景：{scene_type}，{environment}
- 表达方式：{delivery_style}
- 情绪：{emotion}
- 语速：{pace}
- 收音感：{recording_style}
- 自然度：{naturalness}

当前 voice 适配方向：
- 使用 voice: {voice}
- 该 voice 在本轮用于测试：{voice_test_role}

避免：
- {avoid_1}
- {avoid_2}
- {avoid_3}
- {avoid_4}
- {avoid_5}
- {performance_risk}

正文：
{sample_text}
```

其中 `voice_test_role` 不是模型生成，而是工具根据 voice 的定位给出的测试目标：

```json
{
  "Aoede": "自然、轻松、接近日常口播",
  "Kore": "清楚、平稳、不过度强调",
  "Callirrhoe": "明亮、柔和、保持克制",
  "Achernar": "成熟、稳一点、保持真实说话感",
  "Sulafat": "偏年轻、轻快但不要兴奋",
  "Vindemiatrix": "有起伏但需要压低表演感"
}
```

如果 voice 没有内置定位，就使用通用定位：

```text
测试该 voice 是否适合 scene_profile 中的说话人、场景和语速。
```

### 11.2 基础 Prompt 生成不调用模型

Round 1 基础 Prompt 必须是确定性的本地模板生成：

```text
scene_profile + voice_profile + sample_text -> prompt file
```

这一轮不再额外调用文本模型生成 Prompt，避免出现不可控差异。真正需要模型理解的场景判断已经在 `Scene_Profile` 的唯一一次模型调用中完成。

### 11.3 基础 Prompt 的差异来源

Round 1 每个 voice 的基础 Prompt 差异只允许来自：

1. `voice` 名称。
2. `voice_test_role`。
3. 根据 voice 性别/年龄感做的轻微适配。

不允许每个 voice 生成完全不同的剧情、身份或内容。否则 Round 1 就无法公平比较 voice 本身。

Round 1 的目的：

```text
尽量固定内容和风格，只让 voice 变量发生变化，用来筛 voice。
```

Round 2 才允许为 top voice 生成不同风格变体，例如：

1. `closest_reference`
2. `natural_selfie`
3. `calm_clear`

`clean_commercial` 不作为默认变体，避免把 Scene Profile 中的产品、推荐或商业信息进一步放大成广告腔。任何商业、讲解或推荐属性都应先由 `voice_prompt_guidance` 判断其声音表达是否需要自然、克制或正式。

## 12. HTML 是否属于工具职责

如果延续 Analysis_V1 当前边界，工具最终只产出 JSON 和 sample audio。

HTML review 可以作为独立 review artifact，但不作为 TTS Builder 的必要最终产物。建议：

```text
TTS Builder 工具：产出 JSON + samples
Review HTML：独立生成器或 UI 读取 JSON 后展示
```

这和 02_02 的边界保持一致：工具产出机器可消费 JSON，人工检查界面不要混入核心产物合同。

## 13. 声音生成、判断、选择轮次

第一版采用 4 个轮次。这里的“模型调用”分两类：

1. `Scene_Profile` 多模态理解调用：只允许 1 次。
2. Builder-G / Gemini TTS 音频生成调用：允许多次，但每次都必须从 `Prompt/` 中对应文件读取提示词。

### 13.1 Round 0：本地准备，不生成声音

目标：

1. 选择 16 秒主样本窗口，必要时记录 4 秒 / 8 秒对照窗口。
2. 从句子帧本地挑选最多 6 张代表帧。
3. 生成 contact sheet。
4. 写入 `Prompt/00_scene_profile_prompt.md`。
5. 调用一次多模态模型，得到 `scene_profile`。
6. 本地生成 Builder-G 候选 voice 和候选 prompt 草案。

本轮不调用 Gemini TTS。

输出：

```text
S5_03_01_TTSBuilderG/Working/scene_profile_contact_sheet.jpg
S5_03_01_TTSBuilderG/Prompt/00_scene_profile_prompt.md
S5_03_01_TTSBuilderG/Output/scene_profile_response.json
S5_03_01_TTSBuilderG/Output/voice_candidate_plan.json
```

### 13.2 Round 1：8 秒快速试音，筛 voice

目标：

1. 用 8 秒样本快速判断 Builder-G voice 的基础音色是否合适。
2. 避免一开始就对所有 voice 做 16 秒生成，降低运行时长。

候选规模建议：

```text
Gemini voice 候选：6 个
每个 voice：1 个基础 prompt
总 TTS 调用：最多 6 次
样本长度：8 秒
```

Prompt 文件：

```text
S5_03_01_TTSBuilderG/Prompt/round1_voice_001_prompt.txt
...
S5_03_01_TTSBuilderG/Prompt/round1_voice_006_prompt.txt
```

调用规则：

1. 每次 Builder-G 调用只读取对应 `round1_voice_XXX_prompt.txt`。
2. 不允许在代码里追加隐藏语速、情绪、角色说明。
3. 生成 raw sample 后立即测量真实时长。
4. 计算音频特征和初步 Tempo，但 Tempo 只作为筛选信号，不进入最终推荐。

评分重点：

```text
voice_fit_score =
  0.35 * timbre_similarity
  0.25 * pitch_similarity
  0.15 * mandarin_clarity
  0.10 * scene_style_fit
  0.10 * rhythm_fit_after_measured_duration
  0.05 * signal_quality
```

Round 1 只输出 top 3 voice 进入下一轮。

### 13.3 Round 2：16 秒正式候选，筛 prompt + tempo

目标：

1. 对 Round 1 的 top 3 voice 生成正式 16 秒候选。
2. 每个 voice 生成 2 个风格不同的 prompt 变体。
3. 用 Builder-G 真实生成后的音频时长计算 Tempo。

候选规模建议：

```text
voice：3 个
prompt 变体：每个 voice 2 个
总 TTS 调用：最多 6 次
样本长度：16 秒
```

Prompt 文件：

```text
S5_03_01_TTSBuilderG/Prompt/round2_candidate_001_prompt.txt
...
S5_03_01_TTSBuilderG/Prompt/round2_candidate_006_prompt.txt
```

变体建议：

1. `closest_reference`：最贴近原声和原片节奏。
2. `natural_selfie`：更像真实自拍视频，不要播音腔。
3. 如需第三类，可用 `calm_clear`：更清楚一点，但保持日常说话感，不变成讲解腔或广告腔。

Round 2 对每个 raw sample 做：

1. 测真实 `raw_duration`。
2. 计算 `tempo = raw_duration / target_duration`。
3. 判断 Tempo 是否在可接受区间。
4. 生成 fitted sample 作为试听。
5. 计算综合评分。

综合评分：

```text
candidate_score =
  0.28 * voice_fit_score
  0.22 * scene_prompt_fit
  0.18 * timbre_similarity
  0.12 * pitch_similarity
  0.10 * measured_tempo_fit
  0.06 * clarity
  0.04 * duration_safety
```

`measured_tempo_fit` 必须基于 Builder-G 真实生成后的 raw duration，不允许使用本地预估时长。

### 13.4 Round 3：Tempo 修正与最终三候选

Round 3 不扩大搜索，只处理 Round 2 中最好的候选。

处理规则：

1. 如果候选 `0.92 <= tempo <= 1.10`，直接用 atempo 生成 fitted sample。
2. 如果 `1.10 < tempo <= 1.20`，先写一个修正后的 Prompt 文件，要求“语速更快、停顿更短”，再重生成一次。
3. 如果 `0.85 <= tempo < 0.92`，先写一个修正后的 Prompt 文件，要求“语速稍慢、保留自然停顿”，再重生成一次。
4. 如果 `tempo > 1.20` 或 `tempo < 0.85`，仍可生成 fitted sample，但必须标记 `needs_review=true`，不应排在第一名，除非其它候选更差。

修正 Prompt 文件必须另存，不覆盖原始 prompt：

```text
S5_03_01_TTSBuilderG/Prompt/round3_candidate_001_tempo_fix_prompt.txt
```

修正后的 Builder-G 调用也必须只读取这个文件。

最终输出：

```text
SessionOutput/tts/tts_builder_candidate_001.wav
SessionOutput/tts/tts_builder_candidate_002.wav
SessionOutput/tts/tts_builder_candidate_003.wav
SessionOutput/tts/tts_builder_candidates.json
```

最终 `tts_builder_candidate_XXX.wav` 指向 fitted sample，不指向 raw sample。raw sample 只留在工具 Working/Output 审计目录。

### 13.5 最终选择规则

最终 top 3 的排序规则：

1. 第一优先：声音和原参考音色 / 性别 / 年龄感匹配。
2. 第二优先：scene_profile 风格匹配。
3. 第三优先：真实生成后的 Tempo 偏差小。
4. 第四优先：音频清晰、无明显拖腔、无奇怪停顿。
5. 第五优先：三候选之间要有可感知差异，不能只是同一个 voice + 几乎相同 prompt。

如果两个候选分数接近：

1. 保留更自然、更像原片说话方式的。
2. 保留 Tempo 更稳定的。
3. 保留 Prompt 更短、更可迁移到完整 TTS 的。

## 14. 如何和 Gemini 语音模型合作

第一版只使用 Builder-G / Gemini TTS。它和工具的关系是：

```text
工具负责：选样本、选代表帧、生成 Prompt 文件、调用 Gemini、测量音频、评分、拟合 Tempo、输出 JSON。
Gemini 负责：根据指定 voice 和 Prompt 生成 raw TTS 音频。
```

### 14.1 Gemini TTS 调用输入

每次调用必须包含：

1. Gemini TTS model，例如 `gemini-3.1-flash-tts-preview`。
2. Gemini voice，例如 `Aoede`。
3. 一个 Prompt 文件的完整文本。

Prompt 文件里必须包含：

1. 声音方向。
2. 语速方向。
3. 情绪方向。
4. 负面约束。
5. 要朗读的样本文本。

示例：

```text
请用普通话年轻女性声音朗读下面正文，只朗读正文，不要读提示。
声音方向：居家生活短视频口播，近距离自然收音，亲切真实。
语速：略快但清晰，短停顿。
避免：播音腔、过度甜、方言口音、外语口音。

正文：
给我老公买这个润喉糖啊，已经快三个礼拜了啊...
```

代码只允许做：

```text
prompt_text = read_text(prompt_path)
call_gemini_tts(model, voice, prompt_text)
```

不允许做：

```text
call_gemini_tts(model, voice, hidden_prefix + prompt_text + hidden_suffix)
```

### 14.2 Gemini 不负责什么

Gemini TTS 不负责：

1. 选择最终候选。
2. 判断 Tempo 是否正确。
3. 决定是否需要重生成。
4. 修改字幕。
5. 生成完整视频 TTS。

这些都由工具本地逻辑负责。

### 14.3 Gemini 输出后的本地处理

每次 Gemini 生成 raw audio 后，本地必须做：

1. 探测真实时长。
2. 计算 Tempo。
3. 提取音频特征。
4. 和参考音频特征比较。
5. 判断是否需要 Prompt 修正重生成。
6. 生成 fitted sample。
7. 记录本次调用的 prompt_path、voice、model、raw_duration、tempo、fit_duration。

最终 JSON 中不能只写“Gemini 推荐”，必须写本地测量后的证据。

## 15. 最小产物原则

最终给后续大模型和 UI 消费的只需要：

```text
SessionOutput/tts/tts_builder_candidates.json
SessionOutput/tts/tts_builder_candidate_001.wav
SessionOutput/tts/tts_builder_candidate_002.wav
SessionOutput/tts/tts_builder_candidate_003.wav
```

不要把大量中间评分、所有失败候选、provider raw response 放进最终 JSON。

中间审计可以保存在工具自己的 `Output/` 或 `Working/`：

```text
S5_03_01_TTSBuilderG/Output/voice_scoring_audit.json
S5_03_01_TTSBuilderG/Output/sample_duration_comparison.json
S5_03_01_TTSBuilderG/Working/reference_clips/
S5_03_01_TTSBuilderG/Working/raw_candidates/
S5_03_01_TTSBuilderG/Prompt/*.txt
S5_03_01_TTSBuilderG/Prompt/*.md
S5_03_01_TTSBuilderG/Prompt/*.json
```

## 16. 需要回答的问题

实现前需要确认：

1. 工具是否属于 Analysis_V1 主链路，名称是否定为 `03_01_TTSBuilderG.py`？
2. 默认 provider 已确认只做 Builder-G / Gemini，不实现 Builder-Q / Qwen。
3. 最终 JSON 是否只输出 top 3，还是保留 top 6 给 UI 展示？
4. 默认模式是否采用 `8s 粗筛 + 16s 精排`？
5. 是否允许调用多模态模型读取抽样帧来生成 scene_profile 和 prompt？
6. 如果不允许调用模型，是否接受规则模板版 prompt？
7. `sample_audio_path` 默认播放 fitted sample 还是 raw sample？
8. Tempo 是否限制在 `0.85 - 1.20`，超过就标记需人工复核？
9. 是否需要把 4s / 8s / 16s 三个样本的对比结果写入最终 JSON，还是只写入中间审计？
10. 这个工具是否只处理整条视频 / shot 的全局声音，还是未来要支持每个角色或每个场景独立声音？
11. Prompt 文件被人工改过后，rerun 默认是否保留并复用人工 Prompt？

## 17. 推荐第一版范围

第一版建议做成：

1. Analysis_V1 新工具 `03_01_TTSBuilderG.py`，第一版只支持 Builder-G / Gemini TTS。
2. 输入 `final_srt_frame_items.json`、`srt_frames/`、`Audio_Reference.wav`。
3. 默认 `balanced` 模式：8 秒粗筛，16 秒精排。
4. 输出 3 个 Builder-G 候选。
5. 每个候选包含 Gemini voice、prompt、sample、tempo、score、reason。
6. 使用音频特征算法做 voice 排名；Tempo 必须在 Builder-G 真实生成 raw sample 后测量得到。
7. 使用可选 scene_profile 生成 prompt；没有模型时回退到规则模板。
8. 所有模型调用 Prompt 先写入 `S5_03_01_TTSBuilderG/Prompt/`，调用时从文件读取。
9. 最终只产出简洁 JSON 和 3 个平铺 sample wav，不产出 HTML。

第一版不做：

1. 不生成最终全片 TTS。
2. 不锁定用户选择。
3. 不改写字幕。
4. 不做 lip-sync。
5. 不把所有候选和所有 raw response 塞进最终 JSON。
6. 不实现 Builder-Q / Qwen。
