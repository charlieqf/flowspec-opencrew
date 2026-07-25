# Analysis_V1 SRT Detail 工具迁移实现路径

版本：v0.1

状态：实施路径文档。本文只定义 Analysis_V1 后续工具的实现顺序、迁移方式和验收合同，不包含具体代码实现。

## 1. 目标

Analysis_V1 第一阶段只解决一个核心目标：

```text
SRT 拆得准确
每个 SRT 对应的片段准确
每个 SRT 对应的关键帧准确
```

因此第一阶段只实现 `detail` 级别结果，不默认生成 `balanced` 和 `summary`。旧版 `OpenCrew/ToolLibrary/Analysis` 的成熟逻辑作为算法参考，但 Analysis_V1 必须遵守新的 Tool Use Session 合同。

## 2. 总体边界

Analysis_V1 的所有工具必须从第 0 步开始：

```text
OpenCrew/ToolLibrary/Analysis_V1/00_PrepareSessionVariables.py
```

第 0 步完成后，后续工具只允许依赖：

```text
<workspace>/SessionContext/Variables.json
<workspace>/SessionContext/Video_Source.mp4
```

后续工具默认不得：

1. 再访问数据库。
2. 再解析 OpenCode Session。
3. 再读取 workspace 外部原始视频路径。
4. 通过目录扫描猜测全局状态。
5. 把密钥、数据库 URL、access token、cookie 等敏感信息写入任何输出。

## 2.1 原文档验证条款映射

本实现路径必须同时满足以下原文档条款：

1. `Analysis_V1_00_PrepareSessionVariables_工具实现Workbook.md`：00 只产出最小 SessionContext、源视频和自身 Result；00 失败时直接 `blocked`；00 是第一版唯一 DB-aware 工具；`--force` 只清理 00 自有状态。
2. `工具调用会话管理设计PRD.md` 第 5-7 章：后续工具只通过 `SessionContext` 获取全局变量和全局文件；不得依赖原始绝对路径作为唯一输入。
3. `工具调用会话管理设计PRD.md` 第 9-16 章：每个 Tool 必须独立可运行、可依赖自检、可断点续跑、可强制 Rerun；工具目录至少采用 `Working/`、`Output/`、`Report/`；只有存在 LLM / VLM 调用或 Prompt 审计时才创建 `Prompt/`；run 阶段不得跨工具目录直接读取上游文件。
4. `OpenCrew/ToolLibrary/Analysis/AGENT_TOOL_GUIDE.md`：SRT-driven detail flow 以 `01 -> 02 -> 04 -> 05 -> 05_1 -> 05_2 -> 03 -> 08 -> 13_01 -> 13` 为旧版参考；第一阶段不默认生成 balanced / summary。
5. `OpenCrew/ToolLibrary/Analysis_V1/AGENT_TOOL_GUIDE.md`：Analysis_V1 有独立 session contract、workspace layout、sandbox policy 和 rerun behavior，不得用旧 Analysis guide 直接替代 V1 运行规则。

## 3. 推荐执行链路

第一阶段推荐链路：

```text
00 -> 01 -> 02 -> 02_01 -> 03 -> 04 -> 05 -> 06 -> 07 -> 08 -> 09 -> 10 -> 11 -> 12
```

对应旧 Analysis 的 SRT-driven detail flow：

```text
01 -> 02 -> 04 -> 05 -> 05_1 -> 05_2 -> 03 -> 08 -> 13_01 -> 13 -> 14/15/16
```

Analysis_V1 不是简单复制旧编号，而是按新目标重排：

1. 先固定 Session 上下文。
2. 再建立音频字幕证据。
3. 再建立视觉切点和关键帧证据。
4. 再做 ASR/OCR 双向字幕校准。
5. 再生成 detail 语义段。
6. 再绑定每个 segment 的 SRT 和关键帧。
7. 最后做 virtual 导出和质量检查。

## 4. 目录规范

后续每个工具建议采用统一目录形态：

```text
<workspace>/
  SessionContext/
    Variables.json
    Video_Source.mp4

  SessionOutput/
    metadata/
    audio/
    transcript/
    visual/
    subtitle/
    timeline/
    segments/
    schemes/

  SessionReport/
    quality_check.json

  S1_00_PrepareSessionVariables/
    Report/
      Result.json

  S2_01_VideoProbeMetadata/
    Working/
    Output/
    Report/
      Result.json

  S3_02_01_AudioASR/
    Working/
    Output/
    Report/
      Result.json

  S4_02_02_VideoSRTFrame/
    Working/
    Output/
    Report/
      Result.json
```

规则：

1. `SessionContext` 只保存全局变量和全局输入文件。
2. `SessionOutput` 保存跨工具消费的业务产物。
3. 每个 `Sx_*` 目录保存该工具自己的局部输出、日志和执行报告。
4. 每个工具必须写自己的 `Report/Result.json`。
5. 每个 `Result.json` 必须包含 `status`、`tool`、`tool_version`、`inputs`、`outputs`、`warnings`、`blocked_reasons`。
6. 工具间传递以 `SessionOutput` 中的稳定 JSON 为准，不以后续工具扫描 `Sx_*` 私有目录为主。
7. 除 00 的已确认最小实现外，后续工具目录必须具备 `Working/`、`Output/`、`Report/` 三个一层目录；只有调用 LLM / VLM 或需要 Prompt 审计的工具才创建 `Prompt/`，不得为无 Prompt 工具生成空目录。

## 5. P0 工具实现列表

| 顺序 | V1 工具文件 | 参考旧工具 | 目标 | 核心输出 |
|---:|---|---|---|---|
| 00 | `00_PrepareSessionVariables.py` | 已实现 | 固定 Task / Session / Workspace / 模型 / 源视频入口 | `SessionContext/Variables.json`、`SessionContext/Video_Source.mp4` |
| 01 | `01_VideoProbeMetadata.py` | `Analysis/01_video_metadata_extractor.py` | 获取时长、fps、帧数、音轨，为所有时间轴校验提供基础 | `S2_01_VideoProbeMetadata/Output/Video_Metadata.json` -> `SessionContext/Video_Metadata.json` |
| 02_01 | `02_01_AudioASR.py` | `Analysis/02_audio_asr_pipeline.py` | 生成第一版 ASR 字幕和 SRT 时间轴 | `SessionContext/ASR_Segments.json`、`ASR_Raw.srt`、`ASR_Quality.json` |
| 02_02 | `02_02_VideoSRTFrame.py` | 新增 | 按句子 ID 为每句 SRT 绑定对白字幕最清楚的一帧，并用 OCR 形成 ASR/OCR 校准证据 | `SessionOutput/subtitle/final_srt_frame_items.json`、`SessionOutput/visual/srt_frames/` |
| 03_01 | `03_01_TTSBuilderG.py` | `Rebuild_V1/03_02_ShotPlan_GTTSVoiceBuilder.py` | 基于最终逐句对白、句子帧和参考音频，为 Builder-G/Gemini 推荐 3 个候选声音、提示词、样本音频和 Tempo | `SessionOutput/tts/tts_builder_candidates.json`、`SessionOutput/tts/tts_builder_candidate_001.wav` 等平铺样本 |
| 03 | `03_VisualSceneKeyframeExtract.py` | `Analysis/04_pyscenedetect_runner.py` + `Analysis/05_visual_evidence_extractor.py` | 生成视觉切点、场景段、候选关键帧 | `SessionOutput/visual/scene_cuts.json`、`visual_keyframes.json` |
| 04 | `04_VisualOCRSubtitleTimeline.py` | `Analysis/05_1_visual_ocr_timeline_builder.py` | 从画面 OCR 中识别字幕候选和非字幕视觉文字 | `SessionOutput/subtitle/visual_subtitle_timeline.json`、`visual_text_timeline.json` |
| 05 | `05_SubtitleBidirectionalCalibrate.py` | `Analysis/05_2_subtitle_bidirectional_calibrator.py` | ASR 和 OCR 双向校准，形成可信字幕时间轴 | `SessionOutput/subtitle/subtitle_alignment_timeline.json`、`calibrated_srt_items.json` |
| 06 | `06_SemanticSRTStructureBuild.py` | `Analysis/03_semantic_llm_structure_builder.py` | 用 Final Prompt 和校准字幕生成 detail 语义候选段 | `SessionOutput/timeline/semantic_segment_candidates.json` |
| 07 | `07_BoundaryAlignToEvidence.py` | `Analysis/08_boundary_aligner.py` | 将语义边界吸附到字幕、场景切点、视觉关键帧证据 | `SessionOutput/timeline/boundary_alignment.json` |
| 08 | `08_SceneSRTCalibrate.py` | `Analysis/13_01_scene_srt_calibrator.py` | 按场景和字幕校准 detail 时间段 | `SessionOutput/timeline/scene_srt_segments.json` |
| 09 | `09_DetailTimelineBuild.py` | `Analysis/13_fine_timeline_builder.py` | 生成无空洞、无重叠的 detail timeline | `SessionOutput/timeline/scheme_detail_segments.json`、`timeline_coverage_check.json` |
| 10 | `10_SegmentSRTKeyframeBind.py` | `Analysis/14_segment_descriptor_subtitle_builder.py` 的 SRT 裁切和 keyframe 选择逻辑 | 为每个 detail segment 绑定 SRT 和关键帧 | `SessionOutput/segments/segment_XXX.srt`、`segment_XXX_keyframes.json` |
| 11 | `11_VirtualSchemeExportValidate.py` | `Analysis/15_scheme_export_validator.py` | 生成 virtual scheme manifest，不强制真实切片 mp4 | `SessionOutput/schemes/scheme_1/manifest.json` |
| 12 | `12_SRTSegmentKeyframeQualityCheck.py` | `Analysis/16_semantic_first_quality_checker.py` | QA：校验字幕、片段、关键帧、覆盖率、时间边界 | `SessionReport/quality_check.json` |

当前已落地运行链路包含 `00 -> 01 -> 02_01 -> 02_02 -> 03_01`，对应执行目录为 `S1 -> S2 -> S3 -> S4 -> S5`。已经移入 `OpenCrew/ToolLibrary/Analysis_V1/Backup/` 的旧工具只作为历史参考文件保留，不纳入当前 Tool Guide 或正常运行情景。

## 6. 每个工具实现前必须回答的问题

每个工具正式编码前，必须先在实现 PR 或实现记录中回答以下问题。没有回答完，不进入编码。

### 6.1 最小文件与产出物审查

问题：

```text
是否最小程度生成中间文件和产出物？
这些文件是否都是后续工具、页面绑定、QA 或断点续跑真正需要的？
是否存在可以删除的重复 Report、重复 Snapshot、重复 Manifest？
```

判断标准：

1. `SessionContext` 只保存全局变量和全局输入文件。
2. `Working/` 只保存本工具断点续跑、输入快照和缓存所需文件。
3. `Output/` 只保存交给下游消费的最终产物。
4. `Report/` 只保存本工具执行报告、QA 报告或人工可读校验结果。
5. `Prompt/` 只在工具调用 LLM / VLM 或需要 Prompt 审计时创建，并保存提示词、变量、渲染结果和模型响应审计文件；无 Prompt 工具不创建该目录。
6. 不允许因为调试方便而长期保留重复 JSON。

### 6.2 数据库连接审查

问题：

```text
是否需要连接数据库？
如果需要，为什么不能通过 00 写入的 Variables.json 或已有 Output 解决？
```

判断标准：

1. 第一阶段只有 `00_PrepareSessionVariables.py` 允许访问数据库。
2. `01` 到 `12` 默认不得访问数据库。
3. 后续工具需要的 Task、Session、OpenCode Session、模型、Prompt、Attempt 信息，必须来自 `SessionContext/Variables.json`。
4. 如果工具发现 `Variables.json` 信息不足，应返回 `blocked` 并说明缺失字段，不得自行查库补救。

### 6.3 SessionContext 写入审查

问题：

```text
是否需要产出或更新 SessionContext？
如果需要写入 Variables.json，写入字段是什么，谁会消费，为什么不能只放在本工具 Output？
```

判断标准：

1. `00` 必须创建 `SessionContext/Variables.json` 和 `SessionContext/Video_Source.mp4`。
2. `01` 到 `12` 默认只读 `SessionContext`。
3. 只有会被多个后续工具复用的全局状态，才允许写回 `Variables.json`。
4. 写回时必须保留已有字段，不得覆盖其他工具拥有字段。
5. 所有敏感信息禁止写入 `Variables.json` 和 `Result.json`。

### 6.4 产出物与下游消费审查

问题：

```text
本工具产出物是什么？
产出物给后面哪一步使用？
如果产出物缺失，下游应该 blocked、fallback 还是 warning？
```

判断标准：

1. 每个 Output 必须有明确下游消费者。
2. 没有下游消费者的文件只能放入 `Report/` 或不生成。
3. 下游依赖必须在工具自己的 preflight 中显式检查。
4. 下游读取依赖时，应先在 prepare 阶段复制到本工具 `Working/`，run 阶段不得跨工具目录直接读取。

### 6.5 Rerun 与断点续跑审查

问题：

```text
是否按照 Rerun 和断点继续的方式实现？
原始状态是什么？
强制 Rerun 如何恢复到原始状态？
断点续跑如何识别已完成且可信的子步骤？
```

判断标准：

1. 每个工具必须支持 `prepare -> run -> finalize`。
2. 每个工具必须有 `Working/State_progress.json` 或等价状态文件。
3. 断点续跑前必须重新执行依赖自检。
4. 断点续跑只能复用已完成且可信的子步骤。
5. 强制 Rerun 只清理本工具目录下的 `Working/`、`Output/`、`Report/`、`Prompt/`。
6. 强制 Rerun 不得删除上游 Output、其它工具目录、`SessionContext` 中非本工具声明写入的变量。
7. 强制 Rerun 的目标是让本工具回到“从未运行过”的干净状态，而不是清空整个 Tool Use Session。

## 7. P0 工具开工前审查表与测试案例

本章是实现前的强制检查清单。每个工具实现前必须先回答本章对应条目，并为测试案例建立最小自动化测试或 smoke test。

### 7.0 `00_PrepareSessionVariables.py`

开工前回答：

| 问题 | 答案 |
|---|---|
| 是否最小产出 | 是。只允许生成 `SessionContext/Variables.json`、`SessionContext/Video_Source.mp4`、`S1_00_PrepareSessionVariables/Report/Result.json`，以及必要目录。 |
| 是否需要数据库 | 是。仅 00 连接既有 OpenCrew PostgreSQL，用于读取 Task、Session、OpenCode Session、Prompt、Attempt、模型和源视频路径。 |
| 是否产出 SessionContext | 是。00 是唯一必须创建 SessionContext 的工具。 |
| 产出物给谁使用 | `Variables.json` 和 `Video_Source.mp4` 给 `01` 到 `12` 全部后续工具使用；`Result.json` 给 Plan Runner、Debug Console、人工检查使用。 |
| Rerun / 断点续跑 | `--force` 只清理 `SessionContext/`、历史迁移目录 `0_SessionContext/` 和 `S1_00_PrepareSessionVariables/`，然后重建 00 自己的输出；不删除 S2/S3/S4 等后续目录。00 不需要分批断点续跑，但必须具备 blocked 结果落盘能力。 |

测试案例：

1. 成功路径：给定有效 `task_id` 和 `session_id`，生成三个允许文件，`--print-json` 与 `Result.json` 同结构。
2. 最小产出：检查没有生成 `Report.json`、`DependencyCheck.json`、`VariablesSnapshot.json`、`Working/`、`Output/` 等额外文件。
3. DB 阻断：数据库不可连接时返回 `status=blocked`，不启动、不重启、不扫描任何服务。
4. 源视频阻断：源视频不存在或不是 `.mp4` 时返回 `blocked`。
5. 强制 Rerun：预先放置旧 `SessionContext` 和历史迁移目录 `0_SessionContext`，执行 `--force` 后只重建 00 自有状态，不删除 S1/S2 和 `SessionOutput`。
6. 敏感信息：`Variables.json` 和 `Result.json` 不包含 DB URL、password、API key、cookie、auth header。

### 7.1 `01_VideoProbeMetadata.py`

开工前回答：

| 问题 | 答案 |
|---|---|
| 是否最小产出 | 是。核心只需要 `Output/Video_Metadata.json`、`Report/Result.json`、`Working/InputFrom_0_Variables.json`、`Working/State_progress.json`；finalize 后同步到 `SessionContext/Video_Metadata.json`。不生成 md 摘要、Prompt 目录或 SessionOutput。 |
| 是否需要数据库 | 否。读取 `Variables.json.source_video_path` 和 `Video_Source.mp4`。 |
| 是否产出 SessionContext | 是。`Video_Metadata.json` 先产出到本工具 Output，再同步到 `SessionContext/Video_Metadata.json`，并在 `Variables.json.video_metadata_path` 写入指针。 |
| 产出物给谁使用 | `SessionContext/Video_Metadata.json` 给 `03` 关键帧时间边界、`09` timeline coverage、`12` 最终 QA 使用。 |
| Rerun / 断点续跑 | 原始状态是 S2 工具目录不存在或为空。强制 Rerun 只清理 S2 目录和 `SessionContext/Video_Metadata.json`，并移除/重写 `Variables.json.video_metadata_path`。该工具短任务，但支持 fingerprint 一致时 resume 复用 Output。 |

测试案例：

1. 成功路径：从 `SessionContext/Video_Source.mp4` 读取 metadata，输出 duration、fps、frame_count、has_audio。
2. 最小产出：除 `Working/InputFrom_0_Variables.json`、`Working/State_progress.json`、`Output/Video_Metadata.json`、`Report/Result.json`、`SessionContext/Video_Metadata.json` 和 `Variables.json.video_metadata_path` 外，不生成重复文件。
3. 输入隔离：run 阶段只读本工具 `Working/` 快照和 `SessionContext/Video_Source.mp4`。
4. 缺源视频：`Video_Source.mp4` 缺失时返回 `blocked`。
5. 强制 Rerun：旧 Output 和 `SessionContext/Video_Metadata.json` 被清理重建，不影响 `Video_Source.mp4`、其它 Variables 字段和其它工具目录。

### 7.2 `02_01_AudioASR.py`

开工前回答：

| 问题 | 答案 |
|---|---|
| 是否最小产出 | 是。核心只产出 `ASR_Segments.json`、`ASR_Raw.srt`、`ASR_Quality.json`、`Audio_Reference.wav` 和 `Result.json`；音频保留 `Working/Audio_Reference.wav` 供断点续跑，并同步到 `SessionOutput/Audio_Reference.wav` 供页面展示，不创建子目录。 |
| 是否需要数据库 | 需要。`default/cloud` 模式运行时访问 `tool_asr_provider_configs` 读取默认 ASR provider/model/API Key；`local` 模式不读数据库。 |
| 是否产出 SessionContext | 需要。原始 ASR 是后续多工具共享参考输入，必须先写本工具 `Output/`，再同步到 `SessionContext/` 并更新 Variables 指针。 |
| 产出物给谁使用 | `ASR_Segments.json` 和 `ASR_Raw.srt` 给 `05` 校准、`06` 语义结构、`08` fallback、`10` SRT 裁切、`12` QA 使用；`ASR_Quality.json` 给 `04/05/12`；`SessionOutput/Audio_Reference.wav` 给页面播放/校对。 |
| Rerun / 断点续跑 | 原始状态是 S3 目录、本工具 ASR SessionContext 文件、`SessionOutput/Audio_Reference.wav` 和 Variables ASR 指针不存在。`--force` 只清理 S3、本工具 ASR 文件/指针和 `SessionOutput/Audio_Reference.wav`；`--resume` 用视频 fingerprint 和 ASR config signature 复用可信 Output，并重新同步 Audio_Reference。 |

测试案例：

1. 成功路径：输出合法 SRT 和 ASR segment JSON。
2. 时间合法：无负时间、无 `end < start`、按 start 升序。
3. 最小产出：不同时生成多个等价 transcript JSON，不生成 `Prompt/` 或 `SessionOutput/transcript/`。
4. DB 与密钥：`default/cloud` 模式读 DB 获取 Key 但不泄漏 Key；`local` 模式不读 DB。
5. 云端授权前置：自动化计划跑默认云端 ASR 时，必须在 00 传 `--allow-cloud-asr-data-transfer`。02_01 不接受命令级补授权，只认可 00 写入 `Variables.json` 的 `cloud_asr_data_transfer_allowed=true`。
6. 默认云端优先且不自动 fallback：云端 ASR 失败时必须失败或 blocked；只有显式 `--asr-mode local` 或 `--allow-local-fallback` 才能使用本地 Whisper。
7. 断点续跑：可信 resume 不重新读 DB、不重新跑 ASR。
8. 强制 Rerun：清理 S3 自有 Working/Output/Report、本工具 ASR SessionContext 文件/指针和 `SessionOutput/Audio_Reference.wav`，不删除 S2 输出。

### 7.2.1 `02_02_VideoSRTFrame.py`

开工前回答：

| 问题 | 答案 |
|---|---|
| 是否最小产出 | 是。工具最终业务产物只保留 `SessionOutput/subtitle/final_srt_frame_items.json` 和 `SessionOutput/visual/srt_frames/`；每条最终 item 仅包含 `srt_id`、`dialogue`、`image_path`、`start`、`end`、`duration`。逐句映射、句子索引、校准审计和候选帧是本工具中间产物，不作为后续大模型改写输入。 |
| 是否需要数据库 | 否。只读 `SessionContext/Variables.json`、`Video_Metadata.json`、`Video_Source.mp4` 和 `ASR_Segments.json`。 |
| 是否产出 SessionContext | 否。SRT Frame 是业务产物，放入 `SessionOutput/visual/` 和 `SessionOutput/subtitle/`；不改写 02 的原始 ASR。 |
| 产出物给谁使用 | `final_srt_frame_items.json` 给后续 SRT 改写、视觉表达生成和人工校对使用；改写时必须保留 `srt_id` 和句子数量，使图片绑定继续有效。 |
| Rerun / 断点续跑 | 原始状态是 S4 目录和本工具 SessionOutput 产物不存在。`--resume` 在视频、metadata、ASR 和参数签名不变时复用结果；`--force` 只清理本工具目录、`srt_frames/` 和本工具拥有的 subtitle/visual 中间产物。 |

测试案例：

1. 成功路径：每个 ASR segment 生成稳定 `sentence_id`，并绑定一张真实存在的帧图。
2. ID 稳定：输出主键是 `sentence_id`，不是当前 SRT 文本，也不是 frame time；如果 ASR segment 已有 `sentence_id` 必须原样保留。
3. OCR 选择：每句在 ASR 时间窗内抽候选帧，只选字幕 OCR 匹配度、OCR 置信度和字幕区域清晰度综合最高的一帧。
4. 改写兼容：最终 JSON 必须保留稳定 `srt_id`；后续 SRT 改写保持句子数量和 ID 时，图片绑定不需要重建。
5. 最小产出：不创建 `Prompt/`，不调用大模型，不生成 VLM request/response。
6. 强制 Rerun：只删除本工具自己的输出，不删除 S3 ASR、S2 metadata 或其它工具目录。

### 7.3 `03_VisualSceneKeyframeExtract.py`

开工前回答：

| 问题 | 答案 |
|---|---|
| 是否最小产出 | 是。只产出场景切点、视觉边界候选、关键帧索引和必要 jpg 文件；不生成旧版多份 md summary。 |
| 是否需要数据库 | 否。只读视频和 `01` metadata 快照。 |
| 是否产出 SessionContext | 否。关键帧属于业务产物，放在 `SessionOutput/visual/` 和本工具 `Output/`。 |
| 产出物给谁使用 | `scene_cuts.json` 给 `07/08/09/12`；`visual_keyframes.json` 给 `04/07/10/12`；关键帧 jpg 给 OCR、绑定和人工检查使用。 |
| Rerun / 断点续跑 | 原始状态是 S3 目录不存在或为空。长视频抽帧可按批次保存进度；resume 跳过已存在且校验通过的关键帧，force rerun 清理本工具关键帧和索引后重建。 |

测试案例：

1. 成功路径：生成 scene cuts、visual keyframes 和真实存在的 jpg 文件。
2. 时间合法：每个 keyframe time 在 `[0, duration]` 内。
3. 路径合法：keyframe path 为 workspace 相对路径。
4. 最小产出：不生成重复的全局 keyframe 索引。
5. 断点续跑：模拟抽帧中断，resume 后只补缺失帧。
6. 强制 Rerun：删除本工具 Output 和本工具生成的 keyframe 文件，不删除上游 ASR 输出。

### 7.4 `04_VisualOCRSubtitleTimeline.py`

开工前回答：

| 问题 | 答案 |
|---|---|
| 是否最小产出 | 是。只产出 `visual_subtitle_timeline.json`、`visual_text_timeline.json`、必要 OCR 原始缓存和 `Result.json`。 |
| 是否需要数据库 | 否。OCR 引擎和语言配置来自命令参数或本地默认配置。 |
| 是否产出 SessionContext | 否。OCR 结果是下游业务产物，不写全局变量。 |
| 产出物给谁使用 | `visual_subtitle_timeline.json` 给 `05` 字幕校准；`visual_text_timeline.json` 给 `06` 语义结构辅助；OCR 质量信息给 `12` QA 使用。 |
| Rerun / 断点续跑 | 原始状态是 S4 目录不存在或为空。OCR 可按 keyframe 批次 resume；已完成且图片 hash 未变的 OCR 结果可复用。 |

测试案例：

1. 成功路径：对一组关键帧输出字幕候选和非字幕视觉文字。
2. 分类正确：字幕候选与普通视觉文字分开保存。
3. OCR 缺依赖：无 OCR 引擎时返回 `blocked` 或 `completed_with_warning`，不伪造字幕。
4. 断点续跑：模拟部分 keyframe OCR 完成，resume 只处理剩余 keyframe。
5. 最小产出：不把 OCR 大量临时图片复制到 Output。
6. 强制 Rerun：清理 S4 自有 OCR 缓存和输出，不删除 S3 keyframes。

### 7.5 `05_SubtitleBidirectionalCalibrate.py`

开工前回答：

| 问题 | 答案 |
|---|---|
| 是否最小产出 | 是。只产出 `subtitle_alignment_timeline.json`、`subtitle_calibration_decisions.json`、`calibrated_srt_items.json`、`Result.json`。 |
| 是否需要数据库 | 否。只消费 ASR、OCR、metadata 和可选 scene cuts。 |
| 是否产出 SessionContext | 否。校准字幕是业务产物，放 Output 和 `SessionOutput/subtitle/`。 |
| 产出物给谁使用 | `subtitle_alignment_timeline.json` 给 `06/08/09/10/12`；`subtitle_calibration_decisions.json` 给 QA 和人工复核；`calibrated_srt_items.json` 给 SRT 裁切。 |
| Rerun / 断点续跑 | 原始状态是 S5 目录不存在或为空。该工具通常短任务，可重复运行；若分批校准，应保存 item-level progress。force rerun 清理 S5 输出重算。 |

测试案例：

1. 成功路径：ASR + OCR 输入后输出按 start 升序的校准字幕。
2. 冲突标记：ASR/OCR 文本或时间冲突时标记 `needs_review=true`。
3. 时间合法：相邻字幕不异常重叠，且不越过视频边界。
4. fallback：无 OCR 字幕时可基于 ASR 输出 completed_with_warning 或 completed。
5. 最小产出：不同时生成多份同义 calibrated timeline。
6. 强制 Rerun：清理 S5 自有输出，不改动 S2/S4。

### 7.6 `06_SemanticSRTStructureBuild.py`

开工前回答：

| 问题 | 答案 |
|---|---|
| 是否最小产出 | 是。核心只产出 `semantic_segment_candidates.json`、LLM prompt 审计文件、LLM response 审计文件和 `Result.json`。不生成 balanced/summary。 |
| 是否需要数据库 | 否。Final Prompt、模型和 OpenCode Session 引用来自 `Variables.json`。 |
| 是否产出 SessionContext | 否。语义段是业务产物，不写全局变量。 |
| 产出物给谁使用 | `semantic_segment_candidates.json` 给 `07/08/09/12`；Prompt 审计文件给 Debug Console、人工复盘和成本治理使用。 |
| Rerun / 断点续跑 | 原始状态是 S6 目录不存在或为空。LLM 调用必须有幂等输入 hash；resume 时如果 prompt、模型和输入 hash 未变，可复用已完成 response；force rerun 必须显式清理并重新调用。 |

测试案例：

1. 成功路径：生成 detail-only semantic segments。
2. 台词保真：输出不得凭空改写 subtitle text。
3. 引用完整：每个 segment 引用 subtitle item 范围。
4. Prompt 审计：Prompt/ 中有 system/user/variables/rendered prompt 和 response 审计。
5. Resume 防重复调用：模拟 response 已存在且 hash 匹配，resume 不重复调用模型。
6. 强制 Rerun：force 后清理旧 response 并重跑。

### 7.7 `07_BoundaryAlignToEvidence.py`

开工前回答：

| 问题 | 答案 |
|---|---|
| 是否最小产出 | 是。只产出 `boundary_alignment.json`、必要的 rejected/diagnostic 列表和 `Result.json`。 |
| 是否需要数据库 | 否。只消费上游 Output 快照。 |
| 是否产出 SessionContext | 否。边界吸附结果只服务 timeline 构建。 |
| 产出物给谁使用 | `boundary_alignment.json` 给 `08/09/12`；diagnostic 列表给人工检查和 QA。 |
| Rerun / 断点续跑 | 原始状态是 S7 目录不存在或为空。短任务可重复运行；force rerun 清理 S7 自有输出。 |

测试案例：

1. 成功路径：语义边界吸附到字幕或视觉证据。
2. 阈值约束：吸附幅度不超过配置阈值。
3. 时长约束：不制造负时长或交叉 segment。
4. 证据引用：每条调整都有 evidence refs。
5. 上游缺失：缺 `semantic_segment_candidates.json` 时 blocked。
6. 强制 Rerun：清理 S7，不影响 S6。

### 7.8 `08_SceneSRTCalibrate.py`

开工前回答：

| 问题 | 答案 |
|---|---|
| 是否最小产出 | 是。只产出 `scene_srt_segments.json`、`scene_srt_calibration_decisions.json` 和 `Result.json`。 |
| 是否需要数据库 | 否。只消费 scene cuts、subtitle alignment、ASR fallback 和 metadata。 |
| 是否产出 SessionContext | 否。scene-level SRT 是 timeline 中间产物，不写全局变量。 |
| 产出物给谁使用 | `scene_srt_segments.json` 给 `09` detail timeline；decisions 给 `12` QA。 |
| Rerun / 断点续跑 | 原始状态是 S8 目录不存在或为空。短任务可重复运行；force rerun 清理 S8 自有输出。 |

测试案例：

1. 成功路径：按 scene 合成 SRT segment。
2. fallback：无 OCR 校准时使用 ASR，但在 Result 中记录 warning。
3. 时间合法：scene segment 不越界、不负时长。
4. 引用可追溯：每个字幕 item 可追溯到 ASR 或 OCR。
5. 最小产出：不复制整份上游 ASR/OCR 到 Output，只保留必要引用和决策。
6. 强制 Rerun：清理 S8，不影响 S5/S7。

### 7.9 `09_DetailTimelineBuild.py`

开工前回答：

| 问题 | 答案 |
|---|---|
| 是否最小产出 | 是。第一阶段只产出 `scheme_detail_segments.json`、`timeline_coverage_check.json`、`Result.json`；不产出 balanced/summary。 |
| 是否需要数据库 | 否。只消费上游 timeline、metadata、boundary 和字幕。 |
| 是否产出 SessionContext | 否。detail scheme 是业务产物，放 Output 和 `SessionOutput/timeline/`。 |
| 产出物给谁使用 | `scheme_detail_segments.json` 给 `10/11/12`；`timeline_coverage_check.json` 给 `11/12`。 |
| Rerun / 断点续跑 | 原始状态是 S9 目录不存在或为空。短任务可重复运行；force rerun 清理 S9 自有输出。 |

测试案例：

1. 成功路径：输出 detail-only timeline。
2. 覆盖完整：从 0 到 video duration 无空洞。
3. 无重叠：segment 之间无重叠。
4. 字幕绑定：有台词的 segment 都有 subtitle refs。
5. 不生成多 scheme：不生成 balanced/summary 文件。
6. 强制 Rerun：清理 S9，不影响 S8。

### 7.10 `10_SegmentSRTKeyframeBind.py`

开工前回答：

| 问题 | 答案 |
|---|---|
| 是否最小产出 | 是。每个 segment 只产出一个 `.srt`、一个 keyframe binding JSON、一个 segment index JSON 和 `Result.json`；不产出 retake description。 |
| 是否需要数据库 | 否。只消费 detail timeline、calibrated subtitles、visual keyframes 和 source video metadata。 |
| 是否产出 SessionContext | 否。segment 绑定结果属于业务产物。 |
| 产出物给谁使用 | segment SRT 和 keyframe JSON 给 `11` manifest 和 `12` QA；segment index 给页面绑定和人工检查。 |
| Rerun / 断点续跑 | 原始状态是 S10 目录不存在或为空。按 segment 保存进度；resume 跳过已完成且 hash 匹配的 segment，force rerun 清理全部 segment 输出重建。 |

测试案例：

1. 成功路径：每个 detail segment 生成 SRT 和 keyframe JSON。
2. SRT 时间合法：字幕时间落在 segment 时间范围内。
3. keyframe 合法：关键帧落在 segment 内，或明确标记 nearest fallback。
4. 最小产出：不生成 VLM request、retake JSON、压缩图等非 P0 文件。
5. 断点续跑：模拟前半 segment 已完成，resume 只补剩余 segment。
6. 强制 Rerun：清理 S10 segment 输出，不影响 S9 timeline。

### 7.11 `11_VirtualSchemeExportValidate.py`

开工前回答：

| 问题 | 答案 |
|---|---|
| 是否最小产出 | 是。只产出 virtual `manifest.json`、复制或引用的 segment SRT、segment JSON、`Result.json`；不强制产出 `segment_XXX.mp4`。 |
| 是否需要数据库 | 否。只消费 `Variables.json.clip_mode`、source video path、detail segments 和 segment bindings。 |
| 是否产出 SessionContext | 否。manifest 属于交付索引，不写全局变量。 |
| 产出物给谁使用 | `manifest.json` 给页面主入口、`12` QA 和后续下载/分享逻辑；segment JSON/SRT 给页面和用户校对。 |
| Rerun / 断点续跑 | 原始状态是 S11 目录不存在或为空。按 segment manifest item 可断点续跑；force rerun 清理 S11 和 `SessionOutput/schemes/scheme_1` 后重建。 |

测试案例：

1. 成功路径：生成 `SessionOutput/schemes/scheme_1/manifest.json`。
2. virtual 语义：缺少真实 `segment_XXX.mp4` 不失败。
3. manifest 完整：每个 item 包含 source video、start/end、SRT path、keyframe path、JSON path。
4. 页面入口：manifest 可作为唯一主索引，不依赖目录扫描。
5. 断点续跑：模拟部分 segment entry 已完成，resume 补齐。
6. 强制 Rerun：重建 scheme_1，不删除上游 S10。

### 7.12 `12_SRTSegmentKeyframeQualityCheck.py`

开工前回答：

| 问题 | 答案 |
|---|---|
| 是否最小产出 | 是。只产出 `SessionReport/quality_check.json`、本工具 `Report/Result.json`，必要时可产出一个人工可读 QA 摘要。 |
| 是否需要数据库 | 否。只消费 `Variables.json` 和 P0 全链路 Output。 |
| 是否产出 SessionContext | 否。QA 结果不写全局变量。 |
| 产出物给谁使用 | `quality_check.json` 给人工验收、Debug Console、后续 Run / Rerun 决策和页面状态展示。 |
| Rerun / 断点续跑 | 原始状态是 S12 目录不存在或为空。短任务可重复运行；force rerun 清理 S12 和 `SessionReport/quality_check.json` 后重建。 |

测试案例：

1. 成功路径：完整链路产物存在时输出 `status=passed`。
2. 缺文件诊断：缺任一关键产物时输出 `failed` 或 `blocked`，包含 `code`、`affected_file`、`suggested_tool_to_rerun`。
3. SRT 校验：检查每个 segment SRT 不越界、不为空。
4. keyframe 校验：检查每个 keyframe path 存在且时间合法。
5. manifest 校验：检查 virtual manifest 不要求真实 mp4。
6. 不隐式修复：QA 只报告问题，不改写上游产物。

## 8. 每个工具的迁移方式

### 8.1 `01_VideoProbeMetadata.py`

参考：

```text
OpenCrew/ToolLibrary/Analysis/01_video_metadata_extractor.py
```

迁移方式：

1. 保留旧工具的 ffprobe / metadata 解析逻辑。
2. 移除 `--video` 作为主输入的使用习惯。
3. 新工具从 `Variables.json.source_video_path` 解析源视频。
4. 输出从旧版 `meta/` 改为本工具 `Output/Video_Metadata.json`，再在 finalize 阶段同步到 `SessionContext/Video_Metadata.json`。
5. 写入工具自己的 `S2_01_VideoProbeMetadata/Report/Result.json`。

验收点：

1. 能读取 `SessionContext/Video_Source.mp4`。
2. 能输出视频时长、fps、总帧数、音轨存在性。
3. 输出时间单位统一为秒。

### 8.2 `02_01_AudioASR.py`

参考：

```text
OpenCrew/ToolLibrary/Analysis/02_audio_asr_pipeline.py
```

迁移方式：

1. 保留音频提取、Whisper / ASR provider 选择、ASR segment 标准化逻辑。
2. 支持云端和本地 ASR；默认优先且只使用数据库默认云端配置，不自动 fallback 到本地 Whisper。
3. `00` 只把默认 ASR 公共配置写入 `Variables.json.default_asr_config`；`02_01` 调用时再访问数据库获取 API Key。
4. 云端 ASR 如果支持词/字级时间戳，必须优先用 provider 原生 `words[].begin_time/end_time/punctuation` 生成句级 SRT，不得只使用 provider 返回的长 `sentence` 块作为最终 SRT。
5. 输出标准化 ASR JSON 和原始 SRT。
6. SRT item 必须包含 `index`、`start`、`end`、`text`、`confidence`、`source`；如果边界来自 provider 词/字级时间戳，必须额外写入 `time_source="provider_words"`，并可保留该 segment 对应的 `words` 审计字段。
7. ASR 质量报告必须保留，用于决定是否需要 `02_0_AudioSourceSeparation.py`。

实践踩坑与落地规则：

1. DashScope `Recognition.get_sentence()` 返回的 `sentence` 不一定等于业务所需的“单句”。默认可能按 VAD 或较长识别块切分，Task #31 曾出现 64 秒视频只返回 3 个大段的情况。
2. 不允许为了得到短 SRT 而按字符数、文本长度或平均语速估算子句时间。Analysis_V1 第一阶段目标是“SRT 拆得准确”，句级边界必须来自 provider 原生时间戳或后续 OCR/人工校准证据。
3. 对 DashScope 实时识别调用，工具应在不改变数据库模型配置的前提下启用更适合整句识别和时间校准的参数：`semantic_punctuation_enabled=True`、`timestamp_alignment_enabled=True`、`multi_threshold_mode_enabled=True`；中文音频可传 `language_hints=["zh"]`。
4. DashScope `words[].begin_time/end_time` 单位为毫秒，即使数值小于 1000 也仍应按毫秒转换为秒；不能用“大于 1000 才除以 1000”的启发式，否则会把 `200ms` 误读成 `200s` 并导致越界丢弃。
5. 句级聚合规则必须以 provider `words` 为边界来源：遇到 `。！？；` 等完整句标点结束一句；短视频口播场景允许把 `，` 也作为口播短句边界，但边界仍必须取该 word 的 `end_time` 和下一 word 的 `begin_time`，不得估算。
6. 如果 provider 没有返回 `words` 或 `words` 缺少可用时间戳，工具只能保留 provider 原始 `sentence` 时间块，并在 `ASR_Quality.json.warnings` 标记无法生成 provider-word 句级 SRT；不得静默输出估算句级 SRT。
7. 强制重跑 `02_01` 会按工具合同清理 `S3_02_01_AudioASR/`，因此任何人工校验 HTML 或临时报告如果仍需保留，应在重跑后重新生成，或放入调用方明确管理的位置。

验收点：

1. `ASR_Raw.srt` 可直接播放校对。
2. `ASR_Segments.json` 无负时间、无 end 小于 start、无越过视频 duration。
3. `ASR_Raw.srt` 每条 cue 与 `ASR_Segments.json.segments` 同 index、同 start/end/text。
4. `ASR_Quality.json` 能说明覆盖率、静音段、低置信项和 `timeline_alignment.status`。
5. `Result.json` 不泄漏 API Key、数据库 URL 或 Authorization 信息。
6. 云端 ASR 返回 provider `words` 时，`ASR_Segments.json.segments[*].time_source` 应为 `provider_words`，且 SRT 边界必须可追溯到对应 `words` 的 begin/end 时间戳。
7. 对短视频口播样本，不能接受只有少量大段 cue 的输出；如果无法生成句级 cue，必须 warning/blocked 并说明缺少 provider word timestamps，而不是假装通过。

### 8.2.1 `02_02_VideoSRTFrame.py`

参考：

```text
OpenCrew/ToolLibrary/Analysis_V1/Backup/04_VideoSrtOCR.py
OpenCrew/ToolLibrary/Analysis_V1/Backup/03_VideoPySceneDetect.py
```

迁移方式：

1. 不复用旧版 segment 关键帧选择的“按 segment 中点”逻辑；本工具按每句 ASR SRT 时间窗抽候选帧，但最终绑定关系只以 `sentence_id` 为主键。
2. `srt_id` 规则：优先使用 ASR segment 已有 `sentence_id` / `srt_sentence_id`；否则按源 ASR index 生成 `srt_0001`、`srt_0002`。后续 SRT 改写必须保留这些 ID，句子数量不变时 Frame 映射不需要重建。
3. 每个候选帧只对字幕区域 OCR，综合 `text_match_score`、`ocr_confidence`、`subtitle_region_sharpness` 和 `subtitle_center_score` 选出最佳帧。
4. OCR 与 ASR 高度一致时，`final_text` 保留 ASR；OCR 更完整但冲突时可写 `ocr_corrected`，同时 `needs_review=true`。
5. `candidate.time` 只作为审计字段，不作为后续匹配键；最终 JSON 只给下游 `srt_id`、对白、图片地址和时间段。

验收点：

1. 每个有文本的 ASR segment 都有唯一 `srt_id` 和一张 `SessionOutput/visual/srt_frames/<srt_id>.jpg`。
2. `SessionOutput/subtitle/final_srt_frame_items.json` 只包含 `schema_version` 和 `items`，其中每条 item 只包含 `srt_id`、`dialogue`、`image_path`、`start`、`end`、`duration`。
3. 改写 SRT 后，只要句子 ID 和数量不变，后续工具可继续用同一份最终 JSON 和 `srt_frames/`。
4. `Result.json` 不包含数据库 URL、API Key、Authorization、cookie 等敏感信息。
5. 工具不调用大模型，不创建 `Prompt/`。

### 8.3 `03_VisualSceneKeyframeExtract.py`

参考：

```text
OpenCrew/ToolLibrary/Analysis/04_pyscenedetect_runner.py
OpenCrew/ToolLibrary/Analysis/05_visual_evidence_extractor.py
```

迁移方式：

1. 合并旧版 04 和 05 的基础视觉证据能力。
2. 第一版不做 VLM 场景判断，只做本地视觉切点和关键帧提取。
3. 输出场景切点、视觉边界候选、全局关键帧、按场景候选关键帧。
4. 关键帧路径必须是 workspace 相对路径。

验收点：

1. 每个关键帧都有 `time`、`path`、`role`、`source`。
2. 关键帧文件真实存在。
3. 关键帧时间不得超出视频时长。

### 8.4 `04_VisualOCRSubtitleTimeline.py`

参考：

```text
OpenCrew/ToolLibrary/Analysis/05_1_visual_ocr_timeline_builder.py
```

迁移方式：

1. 保留 OCR over keyframes 的逻辑。
2. 明确区分字幕候选和普通视觉文字。
3. 字幕候选进入 `visual_subtitle_timeline.json`。
4. 非字幕视觉文字进入 `visual_text_timeline.json`。
5. OCR 引擎不可用时，工具返回 `blocked` 或 `completed_with_warning`，由实现时统一状态枚举。

验收点：

1. 原视频带硬字幕时，应能产出字幕候选时间轴。
2. 每条 OCR 字幕候选记录来源关键帧时间。
3. OCR 文本清洗不应删除真实台词。

### 8.5 `05_SubtitleBidirectionalCalibrate.py`

参考：

```text
OpenCrew/ToolLibrary/Analysis/05_2_subtitle_bidirectional_calibrator.py
```

迁移方式：

1. 保留 ASR/OCR 双向校准策略。
2. 高质量 ASR 可修正 OCR 文本和时间。
3. 稳定 OCR 字幕可修正 ASR 缺口和漂移。
4. 输出 `subtitle_alignment_timeline.json` 作为后续 SRT 的优先来源。
5. 每条校准记录必须说明 `preferred_source`、`policy`、`needs_review`。

验收点：

1. 校准后的字幕时间轴按 start 升序。
2. 相邻字幕不应异常重叠。
3. 低置信或冲突项必须标记 `needs_review=true`。

### 8.6 `06_SemanticSRTStructureBuild.py`

参考：

```text
OpenCrew/ToolLibrary/Analysis/03_semantic_llm_structure_builder.py
```

迁移方式：

1. 旧版 03 的语义结构生成逻辑可复用，但输入必须改为 V1 的 `Variables.json`、`subtitle_alignment_timeline.json`、`visual_text_timeline.json`。
2. Final Prompt 从 `Variables.json.final_prompt` 获取。
3. 第一版只生成 detail 候选结构，不生成 balanced / summary。
4. LLM 调用的 system prompt、user prompt、上下文摘要、模型信息必须落盘到本工具私有目录，便于审计。

验收点：

1. 输出 segment 候选必须覆盖主要台词。
2. 每个 segment 候选必须引用字幕 item 范围。
3. 不允许凭空改写台词。

### 8.7 `07_BoundaryAlignToEvidence.py`

参考：

```text
OpenCrew/ToolLibrary/Analysis/08_boundary_aligner.py
```

迁移方式：

1. 保留旧版边界吸附算法。
2. 输入包括语义候选边界、字幕校准边界、SceneDetect 切点、视觉关键帧。
3. 本工具只调整边界建议，不直接改写最终 timeline。
4. 每个调整必须记录原始时间、调整后时间、吸附证据和置信度。

验收点：

1. 吸附幅度不得超过配置阈值。
2. 不得制造负时长 segment。
3. 每条边界调整都有 evidence refs。

### 8.8 `08_SceneSRTCalibrate.py`

参考：

```text
OpenCrew/ToolLibrary/Analysis/13_01_scene_srt_calibrator.py
```

迁移方式：

1. 将 scene、SRT、OCR/ASR 校准结果放在同一时间轴里重新对齐。
2. 产出 scene-level SRT segments，作为 detail timeline 的高可信中间层。
3. 优先使用 `subtitle_alignment_timeline.json`，缺失时 fallback 到 `asr_segments.json`。

验收点：

1. 每个 scene segment 都有 subtitle items。
2. scene segment 时间不得超出视频边界。
3. 字幕引用必须可追溯到 ASR 或 OCR 源。

### 8.9 `09_DetailTimelineBuild.py`

参考：

```text
OpenCrew/ToolLibrary/Analysis/13_fine_timeline_builder.py
```

迁移方式：

1. 保留旧版 no-gap/no-overlap timeline 构建逻辑。
2. 第一版只输出 `scheme_detail_segments.json`。
3. 每个 detail segment 必须绑定 subtitle refs、boundary evidence refs、visual evidence refs。
4. 输出 `timeline_coverage_check.json`。

验收点：

1. 从 0 到 video duration 无空洞。
2. segment 之间无重叠。
3. 每个有台词的 segment 都有 subtitle refs。

### 8.10 `10_SegmentSRTKeyframeBind.py`

参考：

```text
OpenCrew/ToolLibrary/Analysis/14_segment_descriptor_subtitle_builder.py
```

迁移方式：

1. 只迁移 SRT 裁切和 keyframe 选择逻辑。
2. 不迁移 retake description / VLM 描述逻辑。
3. 每个 detail segment 产出一个 SRT 文件和一个关键帧绑定 JSON。
4. 关键帧选择优先级建议：
   - segment 内部关键帧。
   - segment start 附近关键帧。
   - segment end 附近关键帧。
   - 最近场景关键帧。
5. 每个绑定必须记录为什么选择该关键帧。

验收点：

1. 每个 detail segment 至少有 1 个关键帧，除非视频读取失败并明确标记。
2. `segment_XXX.srt` 的字幕时间必须落在 segment 时间范围内。
3. 关键帧时间必须落在 segment 内，或明确标记为 nearest fallback。

### 8.11 `11_VirtualSchemeExportValidate.py`

参考：

```text
OpenCrew/ToolLibrary/Analysis/15_scheme_export_validator.py
```

迁移方式：

1. 第一版默认 `clip_mode=virtual`。
2. 不强制导出真实 `segment_XXX.mp4`。
3. manifest 中记录原始 `Video_Source.mp4`、segment start/end、SRT path、keyframe path、JSON path。
4. 输出位置统一为：

```text
SessionOutput/schemes/scheme_1/manifest.json
SessionOutput/schemes/scheme_1/segment_XXX.srt
SessionOutput/schemes/scheme_1/segment_XXX.json
```

验收点：

1. manifest 可作为页面展示主入口。
2. 每个 segment 有 SRT 和 keyframe JSON。
3. virtual 模式下不因缺少 `segment_XXX.mp4` 失败。

### 8.12 `12_SRTSegmentKeyframeQualityCheck.py`

参考：

```text
OpenCrew/ToolLibrary/Analysis/16_semantic_first_quality_checker.py
```

迁移方式：

1. 保留旧版最终 QA 思路，但检查对象改为 V1 的 detail-only 产物。
2. 检查 `Variables.json`、视频 metadata、字幕校准、detail timeline、segment SRT、keyframe JSON、scheme manifest。
3. 输出面向实现者的错误列表，明确哪个工具需要重跑。

验收点：

1. `status=passed` 时，所有 detail segment 都可追溯到源视频、SRT 和关键帧。
2. 失败时，错误必须包含 `code`、`message`、`affected_file`、`suggested_tool_to_rerun`。
3. QA 不做隐式修复，只报告问题。

## 9. P1 可选工具

以下工具不进入第一阶段主链，等 P0 稳定后再实现：

| V1 工具文件 | 参考旧工具 | 触发条件 |
|---|---|---|
| `02_0_AudioSourceSeparation.py` | `Analysis/02_0_source_separation.py` | 背景音乐强、ASR 漂移、讲话被音乐盖住 |
| `06_1_SceneTransitionVLMJudge.py` | `Analysis/06_scene_transition_llm_judge.py` | 多场景、多机位，且必须判断真实空间转换 |
| `07_1_SilentVisualSegmentDetect.py` | `Analysis/07_silent_visual_segment_detector.py` | 长静音但画面仍有重要信息 |
| `09_1_OverCoarseRefine.py` | `Analysis/11_overcoarse_segment_refiner.py` | detail 段过长，需要继续拆 |
| `09_2_OverFragmentMerge.py` | `Analysis/12_overfragmented_segment_merger.py` | detail 段过碎，需要合并 |
| `13_SegmentDescriptorBuild.py` | `Analysis/14_segment_descriptor_subtitle_builder.py` | 需要重拍说明、画面描述、客户交付 JSON |

## 10. 迁移通用规范

每迁移一个旧工具，都按以下顺序做：

1. 阅读旧工具输入、输出和关键函数。
2. 保留可复用算法函数。
3. 移除旧 workspace 目录假设。
4. 移除工具内部数据库访问。
5. 新增读取 `SessionContext/Variables.json` 的入口。
6. 新增读取 `SessionContext/Video_Source.mp4` 的入口。
7. 建立本工具 `Working/`、`Output/`、`Report/` 标准目录；只有存在 LLM / VLM 调用或 Prompt 审计时才建立 `Prompt/`。
8. prepare 阶段把上游依赖复制为 `Working/InputFrom_*` 快照。
9. 输出改写到本工具 `Output/`，再按产物性质同步到 `SessionContext/` 或 `SessionOutput/<domain>/`；仅页面绑定、视觉报告或交付产物进入 `SessionOutput`。
10. 工具私有执行报告写到 `Sx_<ToolName>/Report/Result.json`。
11. `--print-json` 输出必须和 `Result.json` 同结构。
12. 单独运行该工具，确认 downstream 需要的 JSON 稳定存在。

## 11. 测试与验收通用要求

每个工具至少建立以下测试类别：

1. `test_minimal_outputs`：验证只生成已声明的必要产物。
2. `test_no_database_access_after_00`：对 `01` 到 `12`，验证工具不读取 DB URL、不连接数据库。
3. `test_session_context_policy`：验证是否只读或按声明写入 `Variables.json`。
4. `test_downstream_contract`：验证核心 Output 的 schema、路径和下游可读性。
5. `test_blocked_missing_dependency`：缺少必需上游产物时返回 `blocked`，并给出建议重跑工具。
6. `test_force_rerun_scope`：验证 force rerun 只清理本工具拥有的目录和声明产物。
7. `test_resume_or_idempotency`：验证 resume 不重复执行已完成且可信的高成本子步骤。
8. `test_sensitive_output_scan`：验证输出不包含 DB URL、password、API key、cookie、auth header。

测试必须结合工具自己的验收点；不能只检查进程退出码。

## 12. 状态枚举建议

每个工具的 `Result.json.status` 建议统一为：

```text
completed
completed_with_warning
blocked
failed
```

含义：

1. `completed`：工具成功，核心产物完整。
2. `completed_with_warning`：工具成功，但存在低置信、可选能力缺失或需要人工复核。
3. `blocked`：缺少输入、权限、依赖或上游产物，当前工具不应继续。
4. `failed`：代码异常或不可预期错误。

## 13. 第一阶段完成标准

Analysis_V1 第一阶段完成时，至少应满足：

1. 可以从 `00` 连续运行到 `12`。
2. 不需要后续工具访问数据库。
3. 不需要后续工具读取 workspace 外部源视频。
4. 输出一个 detail-only virtual scheme。
5. `SessionOutput/schemes/scheme_1/manifest.json` 可以作为页面主入口。
6. 每个 segment 都有：
   - 时间范围。
   - 对应 SRT。
   - 对应关键帧 JSON。
   - 源视频 virtual playback 指针。
7. `SessionReport/quality_check.json` 能判断整体是否通过。

## 14. 不在第一阶段处理的内容

第一阶段明确不处理：

1. balanced / summary 自动生成。
2. VLM 重拍描述。
3. 真实 mp4 物理切片强制导出。
4. 页面 UI 改造。
5. Plan Runner 自动编排。
6. Attempt 自动创建。
7. 数据库状态回写。
8. 客户分享页权限模型。

这些内容应在 detail-only 主链稳定后，再进入后续 PRD 或实施文档。
