# OpenCut V1 素材库视频语义拆分工具规划

版本：v0.5

状态：历史归档（工具选型与实施路径建议稿）。本文保留 OpenCut 独立性、工具来源和选型依据，不再定义当前工具编号、能力缺口或发布门禁；现行编排以[开发实施设计 v1.1.4](./OpenCrew_素材库综合能力_开发实施设计_v1.md)为准。

## 1. 结论先行

推荐不要继续把新工具直接塞进：

```text
OpenCrew/ToolLibrary/Analysis_V1/
```

而是新建独立工具集：

```text
OpenCrew/ToolLibrary/OpenCut_V1/
```

主要原因：

1. 当前 `Analysis_V1` 已经是一条从参考视频到 TTS、SRT Rewrite、StoryBoard、图片/视频生成和 Composer 的口播生产链。
2. `03_01`、`04_01`、`04_02`、`05_01` 到 `06_01` 等编号均已被生产工具占用。
3. 素材库分析的主实体是原始视频素材，不是口播 Task，也不应为了分析素材创建 StoryBoard。
4. 素材库需要同时保留三套相互独立、可比较的分段结果：SRT、纯视觉、综合；现有 `Analysis_V1` 只保留一条口播业务主链。
5. 新工具应原生遵守 Tool Use Session v0.4 合同，不应继续扩大 `framework_bridge.py` 的 legacy 兼容范围。
6. `OpenCut_V1` 必须是源码和运行时都独立的新工具集；旧工具只作为复制实现时的来源，不构成任何运行依赖。

本项目的主产物不是预先裁切出的 mp4，而是可以精确定位回源视频的 `MediaFragment` 索引。推荐向素材库页面暴露：

```text
SessionOutput/json/srt_semantic_segments.json
SessionOutput/json/visual_semantic_segments.json
SessionOutput/json/composite_semantic_segments.json
SessionOutput/json/dialogue_fragment_index.jsonl
SessionOutput/json/visual_fragment_index.jsonl
SessionOutput/json/composite_fragment_index.jsonl
SessionOutput/json/fragment_quality_annotations.jsonl
SessionOutput/manifests/search_index_manifest.json
```

推荐默认使用：

```text
composite_semantic_segments.json
```

SRT 和纯视觉结果作为可切换的独立证据视图，不应被综合结果覆盖或删除。

### 1.1 产品目标与主实体

上传一个视频后，系统应生成三套彼此独立、允许时间范围重叠的索引与检索单元：

1. `DialogueUnit`：按照对白语义构成的最小不可再拆单元，边界落在句级或词级时间戳上。
2. `VisualUnit`：按照 Keyframe、Shot 和视觉事件语义构成的最小不可再拆单元。
3. `CompositeFragment`：把对白与 Keyframe 证据融合后，供搜索、挑片和剪辑使用的推荐片段。

不存在一套同时适用于对白和画面的“唯一最小切法”。例如一句完整对白可能跨越两个镜头，一个完整动作也可能覆盖两句对白。因此三套边界都应保留，通过统一时间轴相互引用，不能为了整齐而强制对齐。

`MediaFragment` 是索引实体，至少包含：

```text
fragment_id, asset_id, scheme
start, end, duration
dialogue_text, normalized_dialogue
title, summary, keywords
people, objects, scene, action, shot_type
srt_refs, shot_refs, keyframe_refs
usability, exclude_reasons, confidence, needs_review
preview_ref, source_video_ref
```

搜索命中后返回源视频、精确时间范围、命中原因、对白和关键帧预览；只有用户确认使用时，才按需导出物理片段。

## 2. 三种拆分结果的定义

### 2.1 SRT 级逻辑语义拆分

SRT 语义边界只依据：

1. ASR / SRT 句子。
2. 句子级或词级时间戳。
3. 标点、停顿、说话人和语言结构。
4. 对白语义推进，例如问题到回答、观点到论据、问题到解决方案、话题转换和总结。

`03_01_SRTSemanticSegmentBuild` 不读取：

1. 关键帧的视觉语义。
2. SceneDetect 切点。
3. OCR 识别出的画面内容语义。
4. VLM 视觉判断。

这样才能保证分段结果是可审计的“纯对白语义结果”，并能与纯视觉结果独立比较。但在进入语义分段之前，SRT 时间轴与 Keyframe 必须按字幕情况完成证据校准。Keyframe 只校准和绑定 SRT 单元，不参与判断话题、事件或语义边界。

#### 2.1.1 有画面字幕

行为与当前 `Analysis_V1/02_02_VideoSRTFrame.py` 保持一致，但将所需源码复制到 `OpenCut_V1` 后独立运行：

1. 先用 ASR 获得句级/词级对白和时间窗。
2. 在每个 ASR 时间窗内连续抽取候选帧，对字幕区域执行 OCR。
3. 根据 OCR 字幕文字变化识别该 ASR 句子内出现的所有字幕页，而不是每句只取一张图。
4. 用 ASR 对白与 OCR 字幕双向校准文字、页序和时间范围。
5. 每一页字幕必须选择一张最佳 Keyframe，评分至少考虑文字匹配度、OCR 置信度、清晰度和画面位置。
6. 一个 ASR 句子可以拆成多个 `dialogue_unit_id`；每个字幕页对应一个 `dialogue_unit_id` 和一个 Keyframe。
7. 相邻重复字幕页应合并；残留上页文字、OCR 局部缺字和高置信字幕修正沿用现有 Analysis_V1 规则。
8. 最终以校准后的对白单元时间轴作为 `03_01` 输入，Keyframe 只作为该单元的证据。

关系必须保持：

```text
parent_asr_sentence_id
  -> dialogue_unit_id / subtitle_page_id
  -> calibrated_text + start + end
  -> keyframe_path + keyframe_time
```

#### 2.1.2 无画面字幕

1. 不运行字幕 OCR 校准，不因为画面变化新增、删除或移动 SRT 边界。
2. ASR 生成的每个最终 SRT 条目必须对应一个 Keyframe 记录。
3. Keyframe 记录的逻辑边界必须与 SRT 完全一致：`keyframe.start == srt.start`、`keyframe.end == srt.end`。
4. 实际截图时间使用独立字段 `keyframe_time`，并保证位于该 SRT 区间内；优先选择区间内清晰、稳定、非黑帧的代表帧，无法判断时使用中点帧。
5. 视觉 Shot 边界、人物变化、动作变化和场景变化不得改变纯 SRT 方案的分段。
6. 多个 SRT 条目合并为一个对白语义片段后，该片段保留所有成员 SRT 的 Keyframe，不只保留一张封面图。

工具使用 `subtitle_mode=auto|embedded|none`。`auto` 应先生成 `subtitle_presence.json`，根据跨多个对白时间窗的字幕区域 OCR 命中率、文本匹配度和连续性判断；不能因为单帧 OCR 为空就判定无字幕。

核心输出：

```text
srt_keyframe_map.json
calibrated_srt_items.json
srt_semantic_segments.json
```

### 2.2 纯图像理解级视频拆分

只依据连续画面：

1. 硬切、渐变、淡入淡出等镜头边界。
2. 主体、场景、机位、动作、道具和构图变化。
3. 连续 Shot 之间的视觉语义是否仍属于同一个逻辑事件。
4. 视觉上的开始、发展、结束和场景转换。

不读取：

1. 音频。
2. ASR。
3. SRT。
4. 对白文本。
5. OCR 识别出的字幕文本。

如果原视频带硬字幕，纯视觉通道应优先使用遮罩后的画面或裁掉字幕带的关键帧。仅在需要识别标题卡、PPT 或路牌时，才把“非字幕 OCR”作为一个可选视觉文本通道；不能让画面中的字幕把纯视觉结果变成隐形的 SRT 结果。

核心输出：

```text
visual_semantic_segments.json
```

### 2.3 综合视频片段拆分

综合结果读取两条独立结果：

```text
srt_semantic_segments.json
visual_semantic_segments.json
```

再结合：

1. ASR / SRT 边界置信度。
2. 纯视觉语义边界置信度。
3. Shot / Scene 切点。
4. 静音、停顿和说话人变化。
5. 可选的标题卡或非字幕 OCR。
6. 最小时长、最大时长和连续覆盖约束。

核心输出：

```text
composite_semantic_segments.json
```

综合结果必须保留每个边界的证据来源，不能只输出一个无法解释的时间点。

## 3. 当前仓库真实现状

### 3.1 已有 Session 基础设施

以下平台模块已经存在：

```text
OpenCrew/backend/opcrew_backend/tool_sessions/
  io.py
  model_broker.py
  paths.py
  prepare.py
  registry_normalizer.py
  result_sync.py
  runner.py
  schemas/
  service.py
```

已经具备的合同能力：

1. Tool Use Session 根目录。
2. `0_SessionContext` 和输入 Manifest。
3. Tool Registry 归一化。
4. Tool Result、Output Manifest、State、Dependency Check 等 schema。
5. Runner、上下文 Patch ownership 和结果同步框架。
6. 模型调用审计和幂等键的框架。

当前限制：

1. `model_broker.py` 当前主要完成 endpoint 解析、审计、脱敏、幂等和 usage 记录合同；没有在 `submit_model_call()` 中实现真实 provider / OpenCode 请求，未传 `fake_response` 时返回的是占位响应。
2. 旧 `Analysis` 工具还没有原生迁移到 `--tool-session-root` 合同。
3. `Analysis_V1/framework_bridge.py` 是过渡兼容层：它会生成 legacy `SessionContext` 并启动旧式脚本，但不能代替新工具原生的 prepare / run / finalize、输入快照和 broker 合同。

因此，`OpenCut_V1` 新工具应该直接按 v0.4 合同开发；不要再做一层新的 legacy bridge。

### 3.2 `Analysis_V1` 中可复制的基础能力

| 源文件 | 当前状态 | 可复制内容 | 新工具集处理要求 |
| --- | --- | --- | --- |
| `Analysis_V1/00_PrepareSessionVariables.py` | 已实现、支持 framework bridge | Task / Session / Workspace / 源视频准备思路 | 素材库应改为绑定素材分析 Attempt，不应依赖口播专用 Final Prompt |
| `Analysis_V1/01_VideoProbeMetadata.py` | 已实现、支持 force/resume/bridge | ffprobe、OpenCV fallback、视频 fingerprint | 把所需实现复制到 `OpenCut_V1/core/` 后改造；不得从原目录 import |
| `Analysis_V1/02_01_AudioASR.py` | 已实现 | 音频提取、云端/本地 ASR、provider words 句级时间戳、质量报告 | 旧运行路径会直接读 DB 获取 ASR Key；新工具集必须改走受信任 broker / resolver |
| `Analysis_V1/02_02_VideoSRTFrame.py` | 已实现 | 按 ASR 时间窗抽帧、OCR、稳定 `srt_id`、单句多字幕页、逐页 Keyframe、ASR/OCR 双向校准 | `02_03_SRTKeyframeCalibrate.py` 有字幕分支的主要源码复制来源；不是纯视觉语义拆分工具 |
| `Analysis_V1/framework_bridge.py` | 已实现的兼容层 | 旧脚本接入 Tool Result / OutputManifest 的过渡方式 | 不建议作为新工具实现模板 |
| `Analysis_V1/tool_registry.json` | 已实现 | 当前口播生产工具注册表 | 编号已经占满，不应继续加入素材库分段工具 |

### 3.3 旧 `Analysis` 中现成的算法工具

以下文件都真实存在并包含可复制算法，但仍使用旧 workspace / DB / OpenCode / 输出合同。它们属于“算法实现可作为复制来源，但不能作为生产依赖”。

| 旧工具 | 现成能力 | 对新工具的价值 |
| --- | --- | --- |
| `Analysis/01_video_metadata_extractor.py` | 视频 metadata | 已被 V1 版本覆盖，优先用 V1 |
| `Analysis/02_0_source_separation.py` | Demucs 人声/伴奏分离 | 音乐较强、ASR 不稳时的可选预处理 |
| `Analysis/02_audio_asr_pipeline.py` | Whisper / ASR、质量评估 | 算法参考；主路径优先使用 V1 `02_01` |
| `Analysis/03_semantic_llm_structure_builder.py` | 基于 SRT 句级时间轴的语义单元、语义边界和候选段 | SRT 语义拆分的主要源码复制来源 |
| `Analysis/04_pyscenedetect_runner.py` | Content / Adaptive / Threshold SceneDetect | 视觉物理切点基础 |
| `Analysis/05_visual_evidence_extractor.py` | 帧差、HSV histogram、边缘变化、关键帧、视觉边界候选 | 纯视觉证据基础 |
| `Analysis/05_1_visual_ocr_timeline_builder.py` | 字幕 OCR 与普通视觉文字时间轴 | 综合路径的可选补充；不能进入纯视觉主判定 |
| `Analysis/05_2_subtitle_bidirectional_calibrator.py` | ASR / OCR 双向校准 | 硬字幕视频的综合路径可选工具 |
| `Analysis/06_scene_transition_llm_judge.py` | Contact Sheet、分批 VLM、resume、进度、真实拍摄地点转换判断 | 可复制 VLM 调用/批处理骨架，但当前目标只判断 physical location，不等于完整视觉语义分段 |
| `Analysis/07_silent_visual_segment_detector.py` | 检测无对白但有视觉意义的区间 | 综合路径和无对白素材的补充 |
| `Analysis/08_boundary_aligner.py` | 语义边界向视觉/SRT证据吸附 | 综合边界吸附的源码复制来源 |
| `Analysis/09_visual_boundary_promoter.py` | 把高置信视觉边界提升为结构边界 | 综合路径的规则来源 |
| `Analysis/10_evidence_collector.py` | 汇总证据索引 | 综合结果可解释性来源 |
| `Analysis/11_overcoarse_segment_refiner.py` | 拆分过长语义段 | 条件式后处理 |
| `Analysis/12_overfragmented_segment_merger.py` | 合并过碎语义段 | 条件式后处理 |
| `Analysis/13_01_scene_srt_calibrator.py` | Scene 与 SRT 对齐 | 综合路径的中间层参考 |
| `Analysis/13_fine_timeline_builder.py` | 连续时间线、覆盖校验、detail scheme | 最终 timeline 构建的主要源码复制来源 |
| `Analysis/14_segment_descriptor_subtitle_builder.py` | Segment SRT 裁切、关键帧选择、描述 | 只复制 SRT 裁切和关键帧绑定代码，不复制重拍业务描述 |
| `Analysis/15_scheme_export_validator.py` | virtual / physical segment manifest 和导出 | 素材库首版复制 virtual 模式所需代码 |
| `Analysis/16_semantic_first_quality_checker.py` | 最终质量门禁 | 新 QA 工具的源码复制来源 |

### 3.4 已有设计稿但未形成 V1 文件的工具

以下文档已经规划过 `03` 到 `12`：

```text
OpenCrew/docs/SessionDesign-R2/Analysis_V1_SRT_Detail_工具迁移实现路径.md
```

其中的算法映射和 SessionOutput 设计仍然有价值，但不能直接按原编号继续落到当前 `Analysis_V1`，因为现在的 V1 注册表已经使用：

```text
03_01 / 03_02 / 03_03 = TTS
04_01 = SRT Rewrite
04_02 / 04_03 = StoryBoard
05_01 ... 05_06 = 图片/视频计划与执行
06_01 = Composer
```

因此，该迁移稿应作为 `OpenCut_V1` 的源码复制清单和算法参考，而不是继续修改当前口播生产链，也不能让新工具运行时回调原工具。

## 4. 真正缺少的核心能力

### 4.1 缺少纯视觉语义分段工具

现有工具可以找到：

1. 画面硬切。
2. 帧差和视觉变化。
3. 拍摄地点是否变化。
4. 静音区间是否有视觉活动。

但没有工具能够完整回答：

```text
这些连续 Shot 在视觉叙事上是否仍属于同一个逻辑片段？
```

例如：

1. 同一地点从全景切到特写，可能仍是同一个视觉事件，不应拆。
2. 没有硬切但主体动作从“展示产品”变为“操作产品”，可能应该拆。
3. 快速 B-roll 连续切镜可能属于同一个视觉逻辑段，应合并。
4. 同一说话人画面不变，但切入产品细节、PPT 或操作演示，可能形成新视觉段。

因此必须新增：

```text
03_02_VisualSemanticSegmentBuild.py
```

### 4.2 缺少独立的多模态融合工具

现有 `08`、`09`、`13_01` 和 `13` 都是围绕旧 `03` 的语义主时间线做修正。它们没有把：

```text
一套完整 SRT 分段结果
一套完整纯视觉分段结果
```

作为平级输入进行融合，也没有在结果里保留两套方案的边界投票、冲突和取舍原因。

因此必须新增：

```text
04_01_MultimodalSegmentFuse.py
```

这是综合拆分的核心工具，不能只靠 `BoundaryAligner` 改名解决。

### 4.3 缺少面向素材搜索的简介、质量标注和索引发布

现有链路能生成时间线和描述，但还没有把三套分段稳定地发布成素材库可检索实体，也没有统一表达“明显不需要的镜头”。需要增加：

```text
05_02_FragmentDescriptionBuild.py
05_03_FragmentQualityAnnotate.py
05_04_FragmentIndexBuild.py
```

其中：

1. `FragmentDescriptionBuild` 只根据已绑定证据生成短标题、简介、关键词和画面标签，不得修改片段边界。
2. `FragmentQualityAnnotate` 生成 `keep / review / exclude` 及原因、证据和置信度，不物理删除任何时间段。
3. `FragmentIndexBuild` 用确定性代码把三套分段、简介和质量标注物化为 JSONL 与 search manifest，供后端入库和搜索。

旧 `Analysis/14` 的片段描述代码可以复制一部分到新工具集后改造，但质量标注和三路索引发布属于本目标新增的生产能力。

## 5. 推荐的新工具集目录

```text
OpenCrew/ToolLibrary/OpenCut_V1/
  __init__.py
  AGENT_TOOL_GUIDE.md
  tool_registry.json
  pipeline_profiles.json
  requirements-runtime.txt

  00_PrepareSessionVariables.py
  01_VideoProbeMetadata.py
  02_00_AudioSourceSeparation.py
  02_01_AudioASR.py
  02_02_VisualBoundaryEvidence.py
  02_03_SRTKeyframeCalibrate.py
  02_04_VisualOCRTimeline.py
  02_05_SilentVisualSegmentDetect.py
  03_01_SRTSemanticSegmentBuild.py
  03_02_VisualSemanticSegmentBuild.py
  04_01_MultimodalSegmentFuse.py
  04_02_SegmentTimelineFinalize.py
  04_03_SegmentGranularityRefine.py
  05_01_SegmentEvidenceBind.py
  05_02_FragmentDescriptionBuild.py
  05_03_FragmentQualityAnnotate.py
  05_04_FragmentIndexBuild.py
  05_05_VirtualClipManifestBuild.py
  06_01_SegmentQualityCheck.py

  core/
    contracts.py
    media_binaries.py
    media_probe.py
    asr.py
    subtitle_ocr.py
    srt_keyframes.py
    visual_evidence.py
    boundary_fusion.py
    timeline.py

  prompts/
    srt_semantic/
    visual_semantic/
    multimodal_conflict/
```

说明：

1. 编号只在 `OpenCut_V1` 内部生效，不与 `Analysis_V1` 冲突。
2. `core/` 保存纯算法函数，数字开头的 CLI 文件只负责 Tool Use Session 合同、状态、依赖和产物。
3. Prompt 源模板可以在 repo 内维护，但每次运行必须复制到当前 Tool 的 `Prompt/`，并生成 `PromptManifest.json`。
4. 所有借鉴旧工具的实现都必须复制到本目录再改造，不能通过 `importlib`、`sys.path`、软链接或 subprocess 回调旧脚本。

### 5.1 独立工具集的强制隔离规则

`OpenCut_V1` 的交付边界是整个目录。复制完成后，即使把 `ToolLibrary/Analysis/` 和 `ToolLibrary/Analysis_V1/` 从部署环境中移除，新工具集仍必须可以完整运行。

禁止：

1. import `ToolLibrary.Analysis`、`ToolLibrary.Analysis_V1` 或它们的任意模块。
2. 使用 `importlib`、`sys.path.append()`、相对路径穿越或动态加载原工具代码。
3. 通过 subprocess 执行原工具脚本。
4. 读取原工具集的 `tool_registry.json`、`pipeline_profiles.json`、Prompt、Reference、配置或运行产物。
5. 使用指向原工具目录的软链接、硬链接或运行时 fallback。
6. 让新工具的测试依赖原工具目录存在。

允许的依赖只有：

1. Python 标准库和 `OpenCut_V1/requirements-runtime.txt` 中独立声明的第三方包。
2. FFmpeg、FFprobe、Demucs 等明确声明的外部运行时。
3. OpenCrew 平台级 Tool Use Session、Model Broker、文件存储和结果同步合同；这属于平台接口，不属于原工具集依赖。
4. `OpenCut_V1` 自己目录内的 `core/`、Prompt、schema、registry 和 profile。

复制规范：

1. 每个复制文件在文件头记录 `copied_from`、原相对路径、复制日期和后续改造说明，便于追踪来源。
2. 复制后立即替换旧 workspace、DB、OpenCode、Prompt 和输出路径，不保留“原目录不存在时再 fallback”的代码。
3. 公共实现全部收敛到本工具集自己的 `core/`；各 CLI 只能 import 本工具集内部模块。
4. `requirements-runtime.txt`、模型配置 schema、Prompt 和测试样例均在本工具集中独立维护。
5. CI 增加隔离测试：临时隐藏两个原工具目录，仅安装新工具依赖后执行三种 Profile 的 smoke test。

## 6. 推荐实现的工具清单

### 6.1 P0 必须实现

| 顺序 | 新工具 | 实现方式 | 主要输入 | 主要输出 |
| ---: | --- | --- | --- | --- |
| 00 | `00_PrepareSessionVariables.py` | 新工具独立实现；只接平台 S0 合同，不复制口播业务字段 | 素材、Attempt、Workflow Plan | `0_SessionContext/Variables.json`、`Video_Source.mp4`、`InputManifest.json` |
| 01 | `01_VideoProbeMetadata.py` | 复制 V1 所需代码到新目录后独立改造 | 源视频 | `Video_Metadata.json` |
| 02_01 | `02_01_AudioASR.py` | 复制 V1 算法到新目录；模型调用改走 broker/resolver | 视频/音频 | `asr_segments.json`、`asr_sentence_timeline.json`、`asr_quality.json` |
| 02_02 | `02_02_VisualBoundaryEvidence.py` | 复制旧 04 + 05 所需代码到新目录后合并 | 视频 | `shot_boundaries.json`、`visual_boundary_candidates.json`、`visual_keyframes.json` |
| 02_03 | `02_03_SRTKeyframeCalibrate.py` | 复制 `Analysis_V1/02_02_VideoSRTFrame.py` 所需代码到新目录并增加无字幕分支 | 视频 + ASR/SRT | `subtitle_presence.json`、`calibrated_srt_items.json`、`srt_keyframe_map.json`、逐页 Keyframe |
| 03_01 | `03_01_SRTSemanticSegmentBuild.py` | 复制旧 03 所需代码到新目录，删除视觉输入 | 句级 SRT 时间轴 | `srt_semantic_segments.json` |
| 03_02 | `03_02_VisualSemanticSegmentBuild.py` | 新增核心工具；把旧 06 的 contact sheet / batch / resume 骨架复制进新目录再改造 | Shot、关键帧、遮罩帧 | `visual_semantic_segments.json` |
| 04_01 | `04_01_MultimodalSegmentFuse.py` | 新增核心工具；把旧 08/09/10 的所需规则代码复制进新目录再重构 | 两套独立 segments + evidence | `composite_boundary_decisions.json`、`composite_semantic_segments.json` |
| 04_02 | `04_02_SegmentTimelineFinalize.py` | 复制旧 13 所需代码到新目录后独立改造 | 任一 scheme segments | 三套无重叠、可追溯 timeline 和 coverage check |
| 05_01 | `05_01_SegmentEvidenceBind.py` | 复制旧 14 的 SRT/关键帧绑定代码到新目录 | timeline、SRT、keyframes | 每段 SRT、关键帧、证据 JSON |
| 05_02 | `05_02_FragmentDescriptionBuild.py` | 复制旧 14 的相关代码到新目录，并扩展统一简介 schema | 三套 segments + evidence | 标题、简介、关键词、人物/物体/场景/动作标签 |
| 05_03 | `05_03_FragmentQualityAnnotate.py` | 新增；组合对白规则、画质检测和局部模型判断 | segments + ASR + visual evidence | `fragment_quality_annotations.jsonl` |
| 05_04 | `05_04_FragmentIndexBuild.py` | 新增确定性发布工具 | segments + descriptions + quality | 三套 `fragment_index.jsonl`、`search_index_manifest.json` |
| 05_05 | `05_05_VirtualClipManifestBuild.py` | 复制旧 15 virtual mode 所需代码到新目录 | 三套 fragment index | 三套 virtual manifest；不强制输出 mp4 |
| 06_01 | `06_01_SegmentQualityCheck.py` | 复制旧 16 所需代码到新目录后独立改造 | 全链路结果 | `SessionReport/quality_check.json` |

### 6.2 P0 条件工具

| 工具 | 何时运行 | 独立实现方式 |
| --- | --- | --- |
| `02_00_AudioSourceSeparation.py` | 音乐强、ASR 漂移、对白被伴奏覆盖 | 复制旧 `02_0_source_separation.py` 所需代码到新目录后改造 |
| `02_04_VisualOCRTimeline.py` | 综合方案需要标题卡、PPT、路牌或非字幕画面文字 | 复制旧 `05_1_visual_ocr_timeline_builder.py` 所需代码到新目录后改造；不得替代 02_03 的字幕页校准 |
| `02_05_SilentVisualSegmentDetect.py` | 长静音仍有 B-roll、操作演示或动作 | 复制旧 `07_silent_visual_segment_detector.py` 所需代码到新目录后改造 |
| `04_03_SegmentGranularityRefine.py` | 片段过长或过碎 | 把旧 `11` + `12` 所需代码复制到新目录后合并 |

### 6.3 P1 质量增强

| 能力 | 建议 | 原因 |
| --- | --- | --- |
| Shot Boundary 双检测器 | 增加可选 TransNet V2，与 PySceneDetect 取并集后去重 | 对渐变、复杂转场和某些快速运动场景更稳，但会增加模型依赖 |
| 视觉 embedding 时间轴 | 增加 CLIP / SigLIP / DINOv2 embedding，使用 KTS 或 change-point detection 生成候选 | 降低每个边界都调用 VLM 的成本，适合长视频 |
| 多模态冲突裁决 | 只对 SRT 与视觉结果冲突的局部窗口调用 VLM/LLM | 比整条视频重新交给大模型更可审计、更便宜、更易 resume |
| 说话人切换 | 增加 diarization 证据 | 访谈、多人对话和会议素材更需要 |
| 真实物理切片 | 在用户确认片段后再按需 FFmpeg 导出 | 避免分析阶段产生大量重复 mp4 |

## 7. 三条推荐执行路径

### 7.1 路径 A：SRT 逻辑语义拆分

```text
00 Prepare
  -> 01 Metadata
  -> [02_00 Source Separation，条件]
  -> 02_01 Audio ASR
  -> 02_03 SRT Keyframe Calibrate(mode=auto)
  -> 03_01 SRT Semantic Segment Build
  -> 04_02 Timeline Finalize(mode=srt)
  -> 05_01 Evidence Bind
  -> 05_02 Description Build
  -> 05_03 Quality Annotate
  -> 05_04 Index Build
  -> 05_05 Virtual Manifest
  -> 06_01 QA
```

强制规则：

1. `02_03` 必须自动区分有字幕与无字幕，但允许用户显式指定 `embedded` 或 `none`。
2. 有字幕时，每个字幕页必须有独立 `dialogue_unit_id` 和 Keyframe；一个 ASR 句子允许产生多个字幕页单元。
3. 无字幕时，每个最终 SRT 条目必须有一个同边界 Keyframe 记录，`keyframe_time` 只表示区间内实际截图点。
4. `03_01` 可以读取校准后的 SRT 文本与时间，但不读取 Keyframe 图像、视觉标签或 Shot 边界。
5. 最细边界必须落在校准后的句子/词/字幕页时间戳上，不得按字符数估算时间。
6. 不得拆开单个最小校准 SRT 单元。
7. 每段必须记录覆盖的 `srt_id`、`dialogue_unit_id`、句子范围、对白原文、Keyframe 引用和语义边界原因。

### 7.2 路径 B：纯视觉逻辑语义拆分

```text
00 Prepare
  -> 01 Metadata
  -> 02_02 Visual Boundary Evidence
  -> 03_02 Visual Semantic Segment Build
  -> 04_02 Timeline Finalize(mode=visual)
  -> 05_01 Evidence Bind
  -> 05_02 Description Build
  -> 05_03 Quality Annotate
  -> 05_04 Index Build
  -> 05_05 Virtual Manifest
  -> 06_01 QA
```

强制规则：

1. 整条路径不依赖 `02_01`、SRT、ASR、音频或对白文本。
2. 带硬字幕的视频应使用字幕区域遮罩帧做主判断。
3. SceneDetect 切点只是 Shot 候选，不等于最终语义边界。
4. `03_02` 应先把相邻 Shot 组织成视觉窗口，再用 Contact Sheet / 多图输入判断连续 Shot 是否属于同一事件。
5. 每个视觉段必须记录主体、场景、动作、镜头连续性、开始/结束原因和关键帧证据。

### 7.3 路径 C：综合拆分

```text
路径 A 输出 srt_semantic_segments
                 \
                  -> 04_01 Multimodal Segment Fuse
                 /        -> 04_02 Timeline Finalize(mode=composite)
路径 B 输出 visual_semantic_segments
                           -> [04_03 Granularity Refine，条件]
                           -> 05_01 Evidence Bind
                           -> 05_02 Description Build
                           -> 05_03 Quality Annotate
                           -> 05_04 Index Build
                           -> 05_05 Virtual Manifest
                           -> 06_01 QA
```

推荐融合逻辑：

1. 先构建边界候选并集，不直接改写两套源结果。
2. SRT 与视觉边界在容差窗内，例如 `±0.75s`，合并为一个多证据边界。
3. 高置信硬切不必自动升级为语义边界；同一动作的机位切换可以保留在同一综合段。
4. 明显话题变化但画面稳定时，可以使用 SRT 边界。
5. 明显视觉事件变化但对白连续时，可以在不拆开最小 SRT 句子的前提下吸附到最近句界；如果必须切开长句，应标记 `needs_review`，不能静默切断。
6. 无对白视觉段必须由纯视觉结果补入，不能因为 SRT 为空而丢失。
7. 只对高影响冲突调用模型裁决；一致边界走确定性规则。

## 8. `03_02_VisualSemanticSegmentBuild` 的推荐算法

### 8.1 不推荐整条视频一次性给 VLM

不建议把整条原视频直接交给一个 VLM，让它一次输出全部分段并作为唯一真相。

原因：

1. 长视频成本高，失败后难以断点续跑。
2. 时间戳精度和输出稳定性难以控制。
3. 很难逐边界解释证据。
4. 供应商的视频默认采样可能漏掉快速变化。Gemini 官方文档说明视频视觉描述默认按 1 FPS 采样，并明确快速动作可能丢失细节。
5. 直接视频理解通常同时处理音频和视觉，不能满足“纯图像理解”这一独立证据要求。

### 8.2 推荐的两层视觉算法

第一层，本地低成本候选：

```text
PySceneDetect AdaptiveDetector / ContentDetector
  + 当前 VisualEvidenceExtractor 的帧差、HSV、边缘变化
  + 可选 TransNet V2
```

第二层，视觉语义合并：

1. 每个 Shot 选择 start / middle / end 或代表性关键帧。
2. 遮罩字幕区域。
3. 按连续 6～12 个 Shot 构建 Contact Sheet 或多图批次。
4. VLM 只判断相邻 Shot 是否属于同一个视觉事件，以及边界原因。
5. 批次之间保留 1～2 个重叠 Shot，避免窗口边缘判断断裂。
6. 每个 batch 独立落盘、独立 idempotency key、支持 resume。
7. 最终用确定性代码验证连续覆盖、边界顺序、最小时长和最大时长。

现成可复制骨架：

```text
Analysis/06_scene_transition_llm_judge.py
```

需要改写的部分是 Prompt 和输出 schema：从“是否发生物理地点转换”扩展为“是否发生视觉逻辑事件转换”，但仍禁止读取 SRT 和音频。

## 9. `04_01_MultimodalSegmentFuse` 的推荐算法

### 9.1 边界候选数据

每个候选边界统一为：

```json
{
  "candidate_id": "boundary_0008",
  "time": 31.42,
  "sources": ["srt_semantic", "visual_semantic", "shot_cut"],
  "source_refs": ["srt_boundary_004", "visual_boundary_006", "shot_011_end"],
  "scores": {
    "srt_semantic": 0.88,
    "visual_semantic": 0.91,
    "shot_cut": 0.96,
    "pause": 0.42
  },
  "decision": "accepted",
  "final_time": 31.6,
  "reason": "对白话题结束与视觉事件切换在 0.4 秒内一致",
  "needs_review": false
}
```

### 9.2 冲突类型

至少区分：

1. `agreement`：SRT 和视觉同时支持。
2. `srt_only`：话题变化但画面稳定。
3. `visual_only`：视觉事件变化但对白连续或无对白。
4. `shot_only`：只有物理切镜，没有语义变化。
5. `timing_conflict`：两条语义边界含义相同但时间不一致。
6. `granularity_conflict`：SRT 较粗、视觉较细，或反之。
7. `sentence_split_risk`：视觉边界落在一个最小 SRT 句子内部。

### 9.3 裁决顺序

```text
确定性合并和去重
  -> 时间吸附
  -> 最短/最长片段规则
  -> 无对白视觉段补齐
  -> 冲突分类
  -> 仅对高影响冲突调用模型
  -> 连续覆盖校验
```

不建议让 LLM/VLM直接重写整份综合 timeline。模型只返回冲突裁决建议，最终 timeline 由确定性代码生成。

### 7.4 质量标注与过滤规则

分析阶段不物理删除镜头。每个片段或局部时间范围标记为：

```text
keep     默认可搜索、可推荐
review   默认可搜索但提示人工复核
exclude  默认从搜索和推荐结果中过滤，可切换恢复查看
```

建议原因枚举：

| 类别 | 原因 |
| --- | --- |
| 对白 | `false_start`、`stutter`、`filler`、`repetition`、`long_pause`、`truncated_sentence`、`off_topic`、`retake_duplicate` |
| 画面 | `black_frame`、`blur`、`camera_shake`、`occlusion`、`overexposed`、`underexposed`、`focus_hunting`、`camera_setup`、`slate`、`accidental_motion`、`empty_shot` |
| 剪辑 | `pre_roll`、`post_roll`、`setup`、`duplicate_take`、`no_information`、`broken_continuity` |

每条标注必须包含时间范围、reason code、来源工具、证据引用、置信度、默认过滤策略和可读说明。`exclude` 是检索策略，不等于删除源文件，也不应改变三套原始分段结果。

## 10. 推荐输出合同

### 10.1 SRT 与 Keyframe 校准字段

```json
{
  "srt_id": "srt_0018",
  "dialogue_unit_id": "srt_0018_02",
  "parent_asr_sentence_id": "asr_0018",
  "subtitle_mode": "embedded|none",
  "subtitle_page_id": "subtitle_page_0018_02",
  "start": 28.2,
  "end": 31.6,
  "dialogue": "接下来展示实际操作步骤。",
  "asr_text": "接下来展示实际操作步骤",
  "ocr_text": "接下来展示实际操作步骤",
  "keyframe": {
    "keyframe_id": "srt_keyframe_0018_02",
    "start": 28.2,
    "end": 31.6,
    "keyframe_time": 29.84,
    "path": "SessionOutput/visual/srt_frames/srt_0018_02.jpg"
  },
  "calibration": {
    "text_source": "asr_ocr_agree",
    "text_match_score": 0.96,
    "ocr_confidence": 0.94,
    "needs_review": false
  }
}
```

约束：

1. `embedded` 模式下，`dialogue_unit_id` 对应字幕页；一条 ASR 允许输出多条记录。
2. `none` 模式下，`subtitle_page_id`、`ocr_text` 为空，且 Keyframe 的 `start/end` 必须逐值等于 SRT 的 `start/end`。
3. `keyframe_time` 只是图片抓取时间，不是新的逻辑边界。
4. 两种模式统一输出 `calibrated_srt_items.json` 与 `srt_keyframe_map.json`，下游不再根据有无字幕分叉读取不同 schema。

### 10.2 三套 MediaFragment 的公共字段

```json
{
  "fragment_id": "fragment_composite_0007",
  "source_segment_id": "composite_0007",
  "scheme": "srt|visual|composite",
  "start": 28.2,
  "end": 36.8,
  "duration": 8.6,
  "dialogue_text": "接下来展示实际操作步骤。",
  "normalized_dialogue": "接下来展示实际操作步骤",
  "title": "展示产品的实际操作步骤",
  "summary": "从产品外观切换到上手操作并完成第一次演示",
  "keywords": ["产品操作", "上手演示"],
  "visual_labels": {
    "people": ["讲解者"],
    "objects": ["产品"],
    "scene": "室内演示台",
    "action": "拿起并操作产品",
    "shot_type": "中景转特写"
  },
  "boundary_start": {},
  "boundary_end": {},
  "srt_refs": ["srt_0018", "srt_0019"],
  "visual_refs": ["shot_0011", "shot_0012"],
  "keyframe_refs": ["keyframe_0021"],
  "usability": "keep",
  "exclude_reasons": [],
  "confidence": 0.91,
  "needs_review": false,
  "warnings": []
}
```

简介和标签必须来自片段内的对白、关键帧与结构化证据。无法确认的人物身份、品牌或动作不能猜测；应使用通用称谓或留空。

### 10.3 检索索引

建议发布四个逐行可消费产物：

```text
SessionOutput/json/dialogue_fragment_index.jsonl
SessionOutput/json/visual_fragment_index.jsonl
SessionOutput/json/composite_fragment_index.jsonl
SessionOutput/json/fragment_quality_annotations.jsonl
```

搜索采用混合召回和排序：

1. 对白原句、短语和规范化文本的精确匹配。
2. 全文/词法检索，用于近似措辞、关键词和标签。
3. 对简介、对白和视觉标签分别建立语义向量，用于同义表达和“按意思找素材”。
4. 最后叠加可用性、置信度、素材新鲜度和用户筛选条件。

当用户通过对白搜索时，精确与词法命中应优先于通用向量相似度。结果必须说明是“对白原句命中、关键词命中还是语义命中”，并返回 `asset_id + start + end`、片段简介、命中对白和代表 Keyframe。

当前仓库 `asset_search_services.py` 中基于 token 的伪 embedding 只能作为结果结构和排序接口的设计参考；`OpenCut_V1` 不 import 或调用该服务。它本身也不能作为生产级语义检索模型。P0 可先发布 JSONL 并由后端使用数据库全文索引；P1 再接真实文本/多模态 embedding 与向量索引。

### 10.4 Result Index

页面不扫描目录，统一读取：

```text
SessionOutput/manifests/result_index.json
```

建议结构：

```json
{
  "schema_version": "1.0",
  "asset_id": "media_000128",
  "attempt_id": 42,
  "tool_use_session_id": "...",
  "default_scheme": "composite",
  "schemes": {
    "srt": "SessionOutput/json/srt_semantic_segments.json",
    "visual": "SessionOutput/json/visual_semantic_segments.json",
    "composite": "SessionOutput/json/composite_semantic_segments.json"
  },
  "manifests": {
    "srt": "SessionOutput/manifests/srt_virtual_clips.json",
    "visual": "SessionOutput/manifests/visual_virtual_clips.json",
    "composite": "SessionOutput/manifests/composite_virtual_clips.json"
  },
  "search_indexes": {
    "dialogue": "SessionOutput/json/dialogue_fragment_index.jsonl",
    "visual": "SessionOutput/json/visual_fragment_index.jsonl",
    "composite": "SessionOutput/json/composite_fragment_index.jsonl",
    "quality": "SessionOutput/json/fragment_quality_annotations.jsonl",
    "manifest": "SessionOutput/manifests/search_index_manifest.json"
  },
  "quality_report": "SessionReport/quality_check.json"
}
```

## 11. Session 管理必须满足的要求

新工具集直接采用当前 `docs/工具调用会话管理设计PRD.md` v0.4，而不是早期 `SessionDesign-R2/工具调用会话管理设计PRD.md` v0.1 的 workspace 根目录布局。

### 11.1 运行边界

```text
OpenCrew Task / Media Asset 1 : N Attempt
Attempt 1 : 1 Tool Use Session
Tool Use Session 根目录：
<workspace>/tool_use_sessions/<tool_use_session_id>/
```

素材库后端可以让一个素材分析记录承担 Task 等价的运行容器，但 Attempt、Tool Use Session、OpenCode Session 和素材主实体必须是独立字段，不能用文件名隐式推导。

### 11.2 S0 与数据库

1. 后端先创建 Attempt，再创建 Tool Use Session。
2. 只有受信任 S0 / Plan Runner 读取 DB、OpenCode Session 和 provider 配置。
3. 后续工具不得直接访问 DB、OpenCode 管理 API、provider key 或 workspace 外路径。
4. ASR、LLM、VLM 真实调用必须走 broker / resolver。
5. 当前 `model_broker.py` 的真实 provider call path 需要先补齐，不能把占位响应当作完成。

### 11.3 目录与文件

```text
tool_use_sessions/<id>/
  0_SessionContext/
  SessionReport/
  SessionOutput/
  S0_00_PrepareSessionVariables/
  S1_01_VideoProbeMetadata/
  ...
```

1. `0_SessionContext` 平铺，只放全局输入和 Variables。
2. 每个 Tool 有 `State.json`、`Working/`、`Output/`、`Report/`、`Prompt/`。
3. 每个 `Output/` 必须有 `OutputManifest.json`。
4. 模型工具必须有 `PromptManifest.json` 和 ModelCall request/response audit。
5. 页面只读 latest Attempt 的 result index，不扫描历史目录。

### 11.4 prepare / run / finalize

1. prepare 校验依赖和上游 Manifest，并复制依赖到本工具 Working。
2. run 只读取当前 Tool Working、Prompt 和必要的 `0_SessionContext`。
3. run 不跨工具目录、不扫描 stale 文件。
4. finalize 写 Output、Manifest、State、ToolResult 和 Context Patch。
5. Variables Patch 由 Plan Runner 校验 ownership 后合并，Tool 不直接覆盖 Variables。

### 11.5 状态、恢复和幂等

1. 状态至少覆盖 `not_started / preparing / ready / running / partial / completed / failed / dirty / reset`。
2. 长 ASR、VLM batch 和导出任务至少每 60 秒更新 heartbeat。
3. `--resume` 只复用 input hash、config signature 和 Output checksum 均一致的子步骤。
4. 每次模型调用有稳定 idempotency key。
5. `--force-rerun` 只清当前工具目录并 bump retry/idempotency，不删除上游、其它工具或 `0_SessionContext`。
6. 依赖缺失返回标准 `blocked`，不进入 run。

### 11.6 安全与审计

1. 所有 JSON 使用字符串 `schema_version: "1.0"`。
2. InputManifest 记录 sha256、size、visibility、sensitivity。
3. 禁止输出 API Key、DB URL、cookie、Authorization 和本机 secret。
4. Prompt、请求、响应和模型 usage 必须有审计索引。
5. DB 是 Attempt / Step 主状态；workspace 只是输入快照和产物区。
6. DB 同步失败应进入 `completed_with_sync_error`，不能标记完全成功。
7. GC 以 DB 引用为准，先更新 DB 再删除历史文件。

## 12. 推荐的 Profile

### 12.1 `srt_semantic`

```text
00, 01, 02_01, 02_03, 03_01, 04_02, 05_01, 05_02, 05_03, 05_04, 05_05, 06_01
```

条件：`02_00`。`02_03` 本身使用 `subtitle_mode=auto` 选择有字幕或无字幕分支。

### 12.2 `visual_semantic`

```text
00, 01, 02_02, 03_02, 04_02, 05_01, 05_02, 05_03, 05_04, 05_05, 06_01
```

不允许自动插入 ASR / SRT 工具。

### 12.3 `composite_semantic`

```text
00, 01,
02_01, 02_02, 02_03,
03_01, 03_02,
04_01, 04_02,
05_01, 05_02, 05_03, 05_04, 05_05,
06_01
```

条件：`02_00`、`02_04`、`02_05`、`04_03`。

这是素材库默认推荐 Profile。

## 13. 外部方案检索结论

### 13.1 PySceneDetect

官方文档提供 `ContentDetector`、`AdaptiveDetector`、`ThresholdDetector`、`HistogramDetector` 和 `HashDetector`。当前仓库已使用 Content / Adaptive / Threshold，可以继续作为 P0 本地 Shot Boundary 基础。

资料：

- [PySceneDetect Detectors](https://www.scenedetect.com/docs/latest/api/detectors.html)

### 13.2 TransNet V2

TransNet V2 提供预训练 Shot Boundary Detection 推理代码和 PyTorch inference，适合作为 PySceneDetect 的 P1 补充检测器，而不是替代语义分段工具。

资料：

- [TransNet V2 GitHub](https://github.com/soCzech/TransNetV2)
- [TransNet V2 paper](https://arxiv.org/abs/2008.04838)

### 13.3 WhisperX

WhisperX 通过 VAD 和 forced alignment 生成词级时间戳，适合本地 ASR 缺少可靠词级时间时作为可选增强。但当前 `Analysis_V1/02_01_AudioASR.py` 已优先使用 provider 原生 words 时间戳，因此不建议首版立即增加第二套强依赖；先把 WhisperX 作为本地高精度 fallback。

资料：

- [WhisperX paper](https://arxiv.org/abs/2303.00747)
- [WhisperX GitHub](https://github.com/m-bain/whisperX)

### 13.4 CLIP / KTS / Change Point

CLIP 类视觉 embedding 可以把连续关键帧转成视觉特征序列；KTS 或离线 change-point detection 可在不逐边界调用 VLM 的情况下生成视觉语义候选。这适合 P1 长视频成本优化，不是 P0 前置依赖。

资料：

- [CLIP paper](https://arxiv.org/abs/2103.00020)
- [KTS for long-form video understanding](https://arxiv.org/abs/2309.11569)
- [ruptures change-point detection](https://ctruong.perso.math.cnrs.fr/ruptures-docs/build/html/detection/index.html)

### 13.5 直接 VLM 视频理解

Gemini 官方视频理解能力可以描述、分段和引用时间戳，但默认视觉采样约为 1 FPS，同时会处理音频和视觉。适合人工复核或局部冲突裁决，不适合作为“纯图像理解”的唯一主链。

资料：

- [Gemini API Video Understanding](https://ai.google.dev/gemini-api/docs/video-understanding)

## 14. 推荐实施顺序

### 阶段 0：先固化合同

1. 建立 `OpenCut_V1/tool_registry.json`。
2. 建立三种 Profile。
3. 固化字幕检测、SRT-Keyframe 校准、三套 fragment、boundary、description、quality annotation、search index、result index 和 QA schema。
4. 补齐真实 Model Broker / Resolver 调用路径。
5. 建立 10～20 个短视频金标准，包括有字幕纯口播、无字幕纯口播、单句多页字幕、访谈、多机位、B-roll、静音演示和强背景音乐。

### 阶段 1：先跑通两条独立结果

1. 把 00、01、02_01 所需实现复制到 `OpenCut_V1`，移除旧业务与旧目录依赖后独立运行。
2. 把 `Analysis_V1/02_02_VideoSRTFrame.py` 所需实现复制到新工具集，完成 02_03 的有字幕分支。
3. 为 02_03 增加无字幕分支，并验证 Keyframe 边界与 SRT 逐值一致。
4. 实现 03_01 SRT Semantic，并只消费校准后的对白时间轴。
5. 把旧视觉证据实现复制到新工具集，形成独立的 02_02 Visual Evidence。
6. 实现 03_02 Visual Semantic。
7. 两条路径分别完成简介、质量标注、搜索索引、virtual manifest 和 QA。

在两条独立结果稳定前，不开发综合 Fuse，否则无法判断综合结果到底错在上游还是融合。

### 阶段 2：实现综合融合

1. 实现统一 boundary candidate schema。
2. 实现确定性边界合并、吸附、冲突分类和连续覆盖。
3. 实现局部模型裁决。
4. 实现 composite timeline、简介、质量标注、搜索索引、manifest 和 QA。

### 阶段 3：素材检索闭环

1. 把三套 JSONL 索引同步到素材库检索存储。
2. 优先支持对白原句/短语精确检索和全文检索。
3. 接入真实文本与视觉语义 embedding，支持按含义找对白、按画面描述找片段。
4. 搜索默认过滤 `exclude`，提供“包含已过滤镜头”开关。
5. 结果页展示命中原因、源视频时间码、简介、对白与代表 Keyframe。

### 阶段 4：剪辑辅助增强

综合拆分稳定后，再增加：

1. 口误、磕巴、重复、填充词和长停顿检测。
2. 不完整句、抢话和尾音截断检测。
3. 镜头抖动、失焦、遮挡、曝光异常和空镜检测。
4. 候选片段评分。
5. 真实裁切和时间线编辑。

P0 的 `05_03` 先覆盖可确定的高置信规则和明显画质问题；本阶段再增加上下文相关、需要模型判断或人工校正的复杂情况。这些能力应消费稳定的 `segment_id`，不能反过来重新定义素材主实体。

## 15. 验收标准

1. 同一视频能同时产出对白、视觉和综合三套索引，三套片段允许重叠且互不覆盖。
2. 输入一段原始对白或连续短语，可以优先找回包含该对白的最小合适片段，并返回精确起止时间。
3. 输入同义表达或画面描述，可以通过语义索引召回相应片段，并说明命中依据。
4. 每个结果均能回溯到源视频、SRT、Shot 和 Keyframe 证据，不依赖预先物理切片。
5. 标题、简介和标签只描述片段范围内有证据的内容，不产生人物身份、品牌或事件幻觉。
6. 口误、磕巴、重复、空镜、模糊、开拍准备等可标为 `review` 或 `exclude`；默认搜索不返回 `exclude`，但用户可以恢复查看。
7. 质量标注不删除源素材，也不改变原始分段；调整过滤策略后无需重新分析视频。
8. 同一个边界在三套方案中的采用或拒绝都有来源、置信度和裁决理由。
9. 临时移除或改名 `ToolLibrary/Analysis/` 与 `ToolLibrary/Analysis_V1/` 后，`OpenCut_V1` 的 registry、三种 Profile、单元测试和 smoke test 仍全部通过。
10. 对新工具集执行静态扫描，不得出现指向两个原工具目录的 import、动态加载、subprocess、软链接或文件读取路径。
11. 有字幕金标准中，每一页实际出现的字幕都有独立 Keyframe 和 `dialogue_unit_id`，不得把同一 ASR 句子内的多页字幕漏成一页。
12. 无字幕金标准中，每个 SRT 条目都有 Keyframe，且 Keyframe 记录的 `start/end` 与 SRT 逐值一致；Shot 或场景变化不能改变边界。
13. `subtitle_mode=auto` 对有字幕和无字幕金标准的分支判断可解释、可人工覆盖，单帧 OCR 失败不会造成整条视频误判。

## 16. 最终推荐

最优工具路径是：

```text
保留 Analysis_V1 作为口播生产链
保留 Analysis 作为只读的源码复制参考库
使用 backend/opcrew_backend/tool_sessions 的平台合同与执行基础设施
新增完全独立、可单独部署的 ToolLibrary/OpenCut_V1 素材库分析工具集
```

拆分算法层真正需要新增两个核心工具：

```text
03_02_VisualSemanticSegmentBuild.py
04_01_MultimodalSegmentFuse.py
```

纯 SRT 主链还必须把现有校准代码复制并扩展为：

```text
02_03_SRTKeyframeCalibrate.py
  embedded：ASR + OCR 字幕页 + 每页 Keyframe 校准
  none：Keyframe 逻辑边界与 SRT 完全对齐
```

为了完成“搜索素材、对白匹配、简介和废片过滤”的最终目标，还需要新增两个生产工具，并扩展一个描述工具：

```text
05_02_FragmentDescriptionBuild.py      复制相关代码后独立扩展
05_03_FragmentQualityAnnotate.py       新增
05_04_FragmentIndexBuild.py            新增
```

其余 P0 工具主要是把现有 V1 / legacy Analysis 的所需代码复制到新工具集，再按新版 Tool Use Session 合同独立改造。复制只发生在实现阶段；生产运行时两个原工具集必须完全不可见、不可调用、不可读取。

如果只做一个可用的最小版本，优先顺序应是：

```text
ASR
  -> SRT Keyframe Calibrate(auto: embedded|none)
  -> SRT Semantic
  -> Visual Semantic
  -> Composite Fuse
  -> Description
  -> Quality Annotation
  -> Search Index
  -> Virtual Manifest
  -> QA
```

不要先做真实 mp4 切片。首版先把三套时间轴、简介、质量标签和索引做成稳定、可解释、可恢复、可比较的结构化结果；口误与明显废片可以在首版实现高置信规则标注，复杂判断后续增强。这样搜索、挑片和剪辑才能共享同一套可靠的 `MediaFragment` 基础。
