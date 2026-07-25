# DanceMimic_V1 工具目标与实现需求

版本：v0.1

> v0.6 implementation note：本文是早期需求草案。实施以 `DanceMimic_V1_实施收敛设计.md` v0.6 和 `DanceMimic_V1_03_StoryBoardStandardTaskBuild_工具实现需求.md` 为准；若本文出现 `source_reference_video_path`、`provider_module = video_sdr2v_dancemimic`、新增专用 provider 模块等旧口径，均以 v0.6 收敛方案覆盖。

状态：需求草案。本文定义 `DanceMimic_V1` 工具集的目标、工具命名、执行顺序、输入输出和每个工具的实现需求。

## 1. 工具集目标

`DanceMimic_V1` 的核心目标不是重新实现 StoryBoard，而是把输入参考视频预处理成现有 StoryBoard 能直接使用的标准 Task。

主流程：

1. 读取默认视频模型配置，默认模型别名为 `MaxSR2`，显示名可写作 `Max SR2`。
2. 将输入参考视频拆成：
   - 无声音的视频。
   - 人声和音乐混合的音频。
   - 单纯人声的音频。
3. 按 Dialogue 分段参数切分参考视频。
4. 对每个分段视频做人脸检测和跟踪。
5. 用 OpenCV 逐帧绘制黑框、黑底网格、马赛克、强模糊或红色透明网格引导。
6. 输出“已遮住人脸”的分段参考视频。
7. 根据分段结果生成标准 StoryBoard：
   - 1 个 Shot。
   - 1 个 Scene。
   - N 个 Dialogue，N 等于参考视频分段数量。
8. 后续生成、编辑、状态展示和执行全部走现有 StoryBoard、Video Plan、Image Plan、Video Only Plan，以及现有 `05_02 / 05_04 / 05_06` 执行合同。

## 2. 工具列表与命名

推荐工具目录：

```text
OpenCrew/ToolLibrary/DanceMimic_V1/
```

推荐工具列表：

| 步骤 | 工具文件 | 中文名 | 主要职责 |
| --- | --- | --- | --- |
| 00 | `00_PrepareSessionVariables.py` | 准备会话变量 | 准备 Session、读取默认 MaxSR2 模型配置、记录分段参数和输入文件 |
| 01 | `01_ReferenceMediaDemux.py` | 参考视频音画拆分 | 从参考视频拆出无声视频、混合音频、纯人声音频 |
| 02 | `02_ReferenceFaceMaskedVideoBuild.py` | 参考视频人脸遮挡构建 | 按分段参数切视频，做人脸检测/跟踪，逐帧绘制遮盖并生成遮脸参考视频 |
| 03 | `03_StoryBoardStandardTaskBuild.py` | 标准 StoryBoard 构建 | 根据 02 的分段结果写入标准 StoryBoard Task |

推荐步骤目录：

```text
S1_00_PrepareSessionVariables/
S2_01_ReferenceMediaDemux/
S3_02_ReferenceFaceMaskedVideoBuild/
S4_03_StoryBoardStandardTaskBuild/
```

## 3. 总体目录合同

工具集运行完成后，workspace 至少包含：

```text
SessionContext/
  Variables.json
  InputManifest.json
  Video_Reference_Source.mp4

SessionOutput/
  reference/
    Video_Reference_Silent.mp4
    Audio_Reference_Mixed.wav
    Audio_Reference_Vocal.wav
    segments/
      reference_segments_manifest.json
      Segment_0001_Reference_Silent.mp4
      Segment_0001_FaceTrack.json
      Segment_0001_Reference_FaceMasked.mp4
      Segment_0002_Reference_Silent.mp4
      Segment_0002_FaceTrack.json
      Segment_0002_Reference_FaceMasked.mp4

  storyboard/
    srt_storyboard.json
    koubo_storyboard_edit.json
    storyboard_seed.json
    Working/
    assets/
      images/
      videos/
      audios/
      history/
```

规则：

1. `SessionOutput/` 下只允许出现 Session 语义目录，不允许出现工具集名称或工具名目录，例如不得出现 `dance_mimic_v1/`。
2. 跨工具复用的结果放在 `SessionOutput/reference/`、`SessionOutput/reference/segments/`、`SessionOutput/storyboard/` 等语义位置。
3. 单个工具自己的中间状态、调试产物、报告和 Prompt 审计只放在对应 `S{step}_{tool}/Working|Output|Prompt|Report/` 下。
4. 这样保持和 Analysis_V1 一致：Session Output 表示本次 Session 的标准结果，不被具体工具集实现污染。

每个工具仍遵守标准目录：

```text
S{step}_{tool}/
  Working/
  Output/
  Prompt/      # 仅需要 Prompt / 模型调用审计时创建
  Report/
```

## 4. 00_PrepareSessionVariables

### 4.1 定位

`00_PrepareSessionVariables.py` 是 DanceMimic_V1 的第 0 步，负责把后续工具需要的 Session 上下文一次性准备好。

### 4.2 输入

必需输入：

```text
task_id
opencrew_session_id
workspace_dir
reference_video
target_video_seconds
minimum_video_seconds
```

可选输入：

```text
prompt_model_provider / prompt_model_id
run_model_provider / run_model_id
user overrides
```

### 4.3 默认模型配置

00 必须默认读取视频模型别名：

```text
MaxSR2
```

说明：

1. `MaxSR2` 是实现使用的别名。
2. `Max SR2` 可作为界面显示名。
3. 该别名当前对应 Seedance / OpenRouter 的 SR2 multimodal reference-to-video 能力。
4. 00 只写入 provider、model、alias、api_key_ref、has_api_key 等非密钥配置。
5. 真实 API key 不得写入 `Variables.json`、`Result.json`、Prompt、Output 或 stdout。

### 4.4 输出

必须输出：

```text
SessionContext/Variables.json
SessionContext/InputManifest.json
SessionContext/Video_Reference_Source.mp4
S1_00_PrepareSessionVariables/Report/Result.json
```

`Variables.json` 必须包含：

```json
{
  "schema_version": "dance_mimic_v1_session_context_0.1",
  "toolset_id": "DanceMimic_V1",
  "workflow_id": "dance_mimic_v1",
  "task_id": null,
  "opencrew_session_id": null,
  "opencode_session_id": "",
  "workspace_dir": "",
  "source_reference_video_path": "SessionContext/Video_Reference_Source.mp4",
  "storyboard_split_config": {
    "target_video_seconds": 8.0,
    "minimum_video_seconds": 4.0
  },
  "default_video_config": {
    "alias": "MaxSR2",
    "display_name": "Max SR2",
    "provider": "",
    "model": "",
    "api_key_ref": "",
    "has_api_key": false,
    "source": "model_alias"
  },
  "created_at": "",
  "updated_at": ""
}
```

### 4.5 规则

1. 00 是默认 DB-aware 工具。
2. 后续工具默认只读 `Variables.json` 和上游 Output。
3. `target_video_seconds < minimum_video_seconds` 时，00 必须 blocked。
4. 参考视频缺失、不可读或不在 workspace 可访问范围时，00 必须 blocked。

## 5. 01_ReferenceMediaDemux

### 5.1 定位

`01_ReferenceMediaDemux.py` 负责把输入参考视频拆成视频流和音频流，供后续分段、遮挡、StoryBoard 和视频生成参考使用。

### 5.2 输入

prepare 阶段读取：

```text
SessionContext/Variables.json
SessionContext/Video_Reference_Source.mp4
```

run 阶段读取本工具 Working 快照：

```text
S2_01_ReferenceMediaDemux/Working/InputFrom_0_Variables.json
S2_01_ReferenceMediaDemux/Working/Input_Video_Reference_Source.mp4
```

### 5.3 输出

必须输出三类核心文件：

```text
SessionOutput/reference/Video_Reference_Silent.mp4
SessionOutput/reference/Audio_Reference_Mixed.wav
SessionOutput/reference/Audio_Reference_Vocal.wav
```

本工具报告：

```text
S2_01_ReferenceMediaDemux/Report/Result.json
S2_01_ReferenceMediaDemux/Output/media_demux_manifest.json
```

### 5.4 实现需求

1. `Video_Reference_Silent.mp4` 必须保留原参考视频画面、帧率、时长和画幅，不包含音轨。
2. `Audio_Reference_Mixed.wav` 必须包含原视频中的人声和音乐混合音频。
3. `Audio_Reference_Vocal.wav` 必须尽量只保留人声。
4. 音频建议统一为 wav，采样率可按现有工具默认标准，例如 16k 或 44.1k，但必须在 manifest 中记录。
5. 如果原视频没有音轨：
   - `Video_Reference_Silent.mp4` 仍应输出。
   - mixed / vocal 音频输出应 blocked 或生成空音频，具体由 Result 标记，不得静默成功。
6. 纯人声提取可以使用现有音源分离能力或后续接入的本地 / 云端 source separation；真实 provider key 不得落盘。

### 5.5 Result.json 最低字段

```json
{
  "tool": "01_ReferenceMediaDemux",
  "status": "completed",
  "outputs": {
    "silent_video": "SessionOutput/reference/Video_Reference_Silent.mp4",
    "mixed_audio": "SessionOutput/reference/Audio_Reference_Mixed.wav",
    "vocal_audio": "SessionOutput/reference/Audio_Reference_Vocal.wav"
  },
  "warnings": [],
  "blocked_reasons": []
}
```

## 6. 02_ReferenceFaceMaskedVideoBuild

### 6.1 定位

`02_ReferenceFaceMaskedVideoBuild.py` 是核心预处理工具。它按分段参数把无声参考视频切成多个分段，对每段做人脸检测和跟踪，并用 OpenCV 逐帧绘制遮盖，得到每段“遮住人脸后的参考视频”。

### 6.2 输入

prepare 阶段读取：

```text
SessionContext/Variables.json
SessionOutput/reference/Video_Reference_Silent.mp4
```

run 阶段读取本工具 Working 快照：

```text
S3_02_ReferenceFaceMaskedVideoBuild/Working/InputFrom_0_Variables.json
S3_02_ReferenceFaceMaskedVideoBuild/Working/InputFrom_1_Video_Reference_Silent.mp4
```

### 6.3 分段规则

02 使用 `Variables.json.storyboard_split_config`：

```text
target_video_seconds
minimum_video_seconds
```

切分规则与 StoryBoard 适配文档一致：

1. 固定为 1 Shot / 1 Scene 下的 Dialogue 分段。
2. 先根据输入视频总时长生成分段边界。
3. 每段时长不得超过 `target_video_seconds`。
4. 每段时长不得低于 `minimum_video_seconds`。
5. 如果直接按目标时长切分导致尾段过短，必须近似均分，避免 1-2 秒尾段。
6. 分段数量就是后续 StoryBoard Dialogue 数量。

示例：

```text
30s, target=8  -> 8, 8, 8, 6
30s, target=15 -> 15, 15
34s, target=8 且尾段 2s 低于 minimum -> 7, 7, 7, 7, 6
34s, target=15 且尾段 4s 低于 minimum -> 12, 11, 11
```

### 6.4 人脸检测与跟踪

每个分段必须执行：

1. 人脸检测。
2. 人脸框时间线生成。
3. 人脸跟踪和平滑。
4. 遮挡区域扩张，避免边缘漏出。
5. 输出 segment 级 `face_track.json`。

如果检测不到人脸：

1. 该 segment 不应静默失败。
2. 可输出无遮挡版本，但必须在 manifest / Result 中标记 `face_not_detected` warning。
3. 是否阻断后续执行由参数控制，默认建议 warning 而不是 blocked。

### 6.5 逐帧遮盖生成

工具必须基于 segment 级 `FaceTrack.json`，直接在分段视频每一帧的人脸扩张区域绘制遮盖，并输出：

```text
Segment_0001_Reference_FaceMasked.mp4
```

要求：

1. 输出视频无音轨。
2. 输出视频时长、fps、画幅与原分段一致。
3. 人脸区域及必要扩张区域应被黑框、黑底网格、马赛克、强模糊或红色透明网格引导覆盖。
4. 非人脸区域尽量保持原视频内容不变。
5. 合成后的视频作为后续 MaxSR2 / StoryBoard 使用的参考视频。

### 6.6 输出

必须输出：

```text
SessionOutput/reference/segments/reference_segments_manifest.json
SessionOutput/reference/segments/Segment_0001_Reference_Silent.mp4
SessionOutput/reference/segments/Segment_0001_FaceTrack.json
SessionOutput/reference/segments/Segment_0001_Reference_FaceMasked.mp4
...
S3_02_ReferenceFaceMaskedVideoBuild/Report/Result.json
```

`reference_segments_manifest.json` 最低结构：

```json
{
  "schema_version": "dance_mimic_v1_reference_segments_0.1",
  "source_video": "SessionOutput/reference/Video_Reference_Silent.mp4",
  "target_video_seconds": 8.0,
  "minimum_video_seconds": 4.0,
  "segments": [
    {
      "segment_id": "segment_0001",
      "dialogue_asset_key": "dak_0001",
      "index": 1,
      "start": 0.0,
      "end": 8.0,
      "duration": 8.0,
      "silent_video_path": "SessionOutput/reference/segments/Segment_0001_Reference_Silent.mp4",
      "face_track_path": "SessionOutput/reference/segments/Segment_0001_FaceTrack.json",
      "face_masked_reference_video_path": "SessionOutput/reference/segments/Segment_0001_Reference_FaceMasked.mp4",
      "warnings": []
    }
  ]
}
```

## 7. 03_StoryBoardStandardTaskBuild

### 7.1 定位

`03_StoryBoardStandardTaskBuild.py` 负责把 02 的分段结果写成现有 StoryBoard 可直接打开的标准 Task。

### 7.2 输入

```text
SessionContext/Variables.json
SessionOutput/reference/Audio_Reference_Mixed.wav
SessionOutput/reference/Audio_Reference_Vocal.wav
SessionOutput/reference/segments/reference_segments_manifest.json
```

### 7.3 输出

必须输出：

```text
SessionOutput/storyboard/srt_storyboard.json
SessionOutput/storyboard/koubo_storyboard_edit.json
SessionOutput/storyboard/assets/videos/
SessionOutput/storyboard/assets/audios/
SessionOutput/storyboard/Working/
S4_03_StoryBoardStandardTaskBuild/Report/Result.json
```

### 7.4 StoryBoard 结构

必须固定生成：

```text
1 个 Shot
1 个 Scene
N 个 Dialogue
```

其中：

1. `N = reference_segments_manifest.segments.length`。
2. 如果参考视频被切成 3 个片段，则生成 3 个 Dialogue。
3. 如果参考视频被切成 4 个片段，则生成 4 个 Dialogue。
4. 如果参考视频被切成 2 个片段，则生成 2 个 Dialogue。
5. 每个 Dialogue 的 `start / end / duration` 与对应参考视频分段一致。

### 7.5 Dialogue 字段要求

每个 Dialogue 至少包含：

```json
{
  "dialogue_asset_key": "dak_0001",
  "dialogue_id": "dlg_0001",
  "srt_id": "",
  "dialogue_index": 1,
  "dialogue": "片段 01",
  "start": 0.0,
  "end": 8.0,
  "duration": 8.0,
  "working_assets": {
    "videos": [],
    "audio": null,
    "images": []
  }
}
```

规则：

1. `dialogue_asset_key` 必须来自 02 manifest 或由 03 稳定生成。
2. `dialogue_id` 用于页面编辑对象。
3. `srt_id` 可为空，因为本工具不依赖字幕来源。
4. `dialogue` 可以使用占位文案，例如 `片段 01`。
5. 时间必须与分段视频一致。

### 7.6 参考视频与音频绑定

03 必须把 02 生成的遮脸参考视频作为 StoryBoard 可用素材。

推荐落点：

```text
SessionOutput/storyboard/assets/videos/{dialogue_asset_key}_Reference_FaceMasked.mp4
```

03 必须把 01 生成的音频作为 StoryBoard 可用素材。

推荐落点：

```text
SessionOutput/storyboard/assets/audios/Audio_Reference_Mixed.wav
SessionOutput/storyboard/assets/audios/Audio_Reference_Vocal.wav
```

绑定策略：

1. 遮脸参考视频进入 `assets/videos/`，作为后续 MaxSR2 / Video Plan 可选择的参考视频。
2. 如果后续计划需要每个 Dialogue 对应单独参考视频，03 应在 Dialogue 或 plan seed 中记录对应 segment 的 `face_masked_reference_video_path`。
3. 混合音频和纯人声音频都进入 `assets/audios/`，供后续执行器或模型输入选择。
4. 不得把这些参考视频误写成当前 Final Video。
5. Raw / Final 仍由后续 Video Plan / Video Only Plan 执行后产生。

### 7.7 Plan 种子

03 可以生成后续 plan 所需的 seed 信息，但不得替代现有 Plan 合同。

建议输出：

```text
SessionOutput/storyboard/storyboard_seed.json
```

包含：

```json
{
  "segments": [
    {
      "dialogue_asset_key": "dak_0001",
      "reference_video_path": "SessionOutput/storyboard/assets/videos/dak_0001_Reference_FaceMasked.mp4",
      "reference_video_source_path": "SessionOutput/reference/segments/Segment_0001_Reference_FaceMasked.mp4",
      "video_generation_mode": "seedance_sdr2v_dancemimic",
      "provider_module": "video_sdr2v_dancemimic",
      "prompt_template": "Video_SDR2V_DanceMimic.md",
      "mixed_audio_path": "SessionOutput/storyboard/assets/audios/Audio_Reference_Mixed.wav",
      "vocal_audio_path": "SessionOutput/storyboard/assets/audios/Audio_Reference_Vocal.wav",
      "start": 0.0,
      "end": 8.0,
      "duration": 8.0
    }
  ],
  "default_video_model_alias": "MaxSR2"
}
```

后续 `video_generation_plan.json`、`image_generation_plan.json`、`video_only_generation_plan.json` 仍按现有 StoryBoard / Plan 工具生成。

DanceMimic_V1 的视频生成计划必须保留每个 Dialogue 对应的遮脸参考视频路径，并默认路由到 Seedance SDR2V DanceMimic 专用模块：

```text
OpenCrew/ToolLibrary/Analysis_V1/video_plan_executor_modules/video_sdr2v_dancemimic.py
OpenCrew/ToolLibrary/Analysis_V1/Reference/05_02/Video_SDR2V_DanceMimic.md
```

执行时：

1. 首帧图片来自当前 Dialogue 的 `Image_New` / `Image_Source` / 已物化首帧。
2. 参考视频来自当前 Dialogue 对应的 `Reference_FaceMasked.mp4`。
3. 不得使用全量参考视频或其它 Dialogue 的参考视频。
4. 缺首帧或缺对应参考视频时，当前 segment 必须 blocked。

## 8. 与现有 StoryBoard 后续链路的关系

03 完成后，后续全部走现有 StoryBoard：

1. 用户从 Task List 打开现有 StoryBoard 页面。
2. StoryBoard 展示 1 Shot / 1 Scene / N Dialogue。
3. 用户可以继续编辑 Shot / Scene / Dialogue。
4. 用户可以继续使用现有 Video Plan / Image Plan / Video Only Plan。
5. 执行仍复用 `05_02 / 05_04 / 05_06` 的标准回写合同。
6. `05_02 / 05_06` 在 DanceMimic 任务中通过专用 `video_sdr2v_dancemimic.py` 调用 Seedance reference-video + first-frame 能力。

## 9. 验收要求

### 9.1 00 验收

1. `Variables.json` 存在。
2. 默认 video config alias 为 `MaxSR2`。
3. `target_video_seconds` 和 `minimum_video_seconds` 已写入。
4. API key 未落盘。

### 9.2 01 验收

1. 无声视频存在且时长与源视频一致。
2. mixed 音频存在。
3. vocal 音频存在或明确 warning / blocked。
4. manifest 记录音视频编码、时长、采样率。

### 9.3 02 验收

1. 分段数量符合参数切分规则。
2. 每段时长满足 `minimum_video_seconds <= duration <= target_video_seconds`。
3. 不出现 1-2 秒极短尾段，除非用户设置的 minimum 允许。
4. 每段输出 silent segment、face track、face masked reference video。
5. face masked reference video 与分段视频时长、fps、画幅一致。
6. 输出视频无音轨，人脸区域被遮挡。

### 9.4 03 验收

1. `srt_storyboard.json` 可被现有 StoryBoard 打开。
2. 固定 1 Shot / 1 Scene。
3. Dialogue 数量等于 02 分段数量。
4. 每个 Dialogue 的时间与分段视频一致。
5. 每个 Dialogue 有稳定 `dialogue_asset_key`。
6. 遮脸参考视频进入 StoryBoard assets。
7. Task List 可进入现有 StoryBoard 页面。

### 9.5 后续 05_02 / 05_06 验收

1. Video Plan / Video Only Plan 能为 DanceMimic segment 写入 `provider_module = video_sdr2v_dancemimic`。
2. 每个 segment 的 `reference_video_path` 与 02 拆分结果一一对应。
3. 每个 segment 的首帧图片来自当前 Dialogue 的标准图片槽位或已物化首帧。
4. 运行 `05_02` 时，Seedance SDR2V 使用首帧 + 当前分段参考视频生成 Final 视频并回写 StoryBoard。
5. 运行 `05_06` 时，Seedance SDR2V 使用首帧 + 当前分段参考视频生成 Raw 视频，Confirm Final 后才绑定 Final。
6. 缺少首帧或缺少当前分段参考视频时，当前 segment blocked。

## 10. 参考文档

1. `OpenCrew/docs/DanceMimic_V1/DanceMimic_V1_标准Session管理与工具实现规范.md`
2. `OpenCrew/docs/DanceMimic_V1/DanceMimic_V1_StoryBoard标准Task适配问题集.md`
3. `OpenCrew/docs/DanceMimic_V1/DanceMimic_V1_02_ReferenceFaceMaskedVideoBuild_工具研究与设计.md`
4. `OpenCrew/docs/DanceMimic_V1/DanceMimic_V1_后续执行_Seedance_SDR2V_DanceMimic_适配需求.md`
5. `OpenCrew/docs/SessionDesign-R2/STORYBOARD_OUTPUT_STRUCTURE.md`
6. `OpenCrew/docs/SessionDesign-R2/Analysis_V1_05_01_VideoGenerationPlan_工具需求整理.md`
7. `OpenCrew/docs/SessionDesign-R2/Analysis_V1_05_03_05_04_ImagePlan_工具需求整理.md`
8. `OpenCrew/docs/SessionDesign-R2/Analysis_V1_05_05_05_06_VideoOnlyPlan_工具需求整理.md`
