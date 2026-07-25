# Analysis_V1 05_01_VideoPlanGenerator 工具需求整理

版本：v0.3

状态：最终确认版。本文用于指导 `05_01_VideoPlanGenerator.py` 的实现与测试。

## 1. 背景

`04_02_StoryBoard` 已经把改写后的 SRT 组织成：

```text
Shot -> Scene -> Dialogue
```

下一步需要从已经选定的 StoryBoard 范围中，自动规划后续视频生成链路。

这里的“规划”不是直接生成音频、图片或视频，而是把一个 Scene、一个 Shot 或整个 Task ShotPlan 拆成一组可以被后续工具执行的任务行。

每一行任务回答：

1. 覆盖哪些 Dialogue。
2. 是否需要生成 Dialogue 音频。
3. 是否需要从旧图生成图片提示词。
4. 是否需要生成新图片。
5. 是否需要生成视频提示词。
6. 是否需要生成视频。
7. 这个视频片段的首帧来自哪里。
8. 如果无法继续，为什么 skipped 或 blocked。

## 2. 工具定位

建议新增工具：

```text
05_01_VideoPlanGenerator.py
```

推荐 Tool Use Session 步骤目录：

```text
S8_05_01_VideoPlanGenerator/
```

如果该工具被插入其它工具链，`S8` 只是当前 Analysis_V1 主链路中的推荐顺序；真实运行时仍遵守 `S{step_index}_{tool_name}` 规则。

当前主链路变为：

```text
S1_00_PrepareSessionVariables  <- 00_PrepareSessionVariables.py
S2_01_VideoProbeMetadata       <- 01_VideoProbeMetadata.py
S3_02_01_AudioASR              <- 02_01_AudioASR.py
S4_02_02_VideoSRTFrame         <- 02_02_VideoSRTFrame.py
S5_03_01_TTSBuilderG           <- 03_01_TTSBuilderG.py
S6_04_01_SRTRewrite            <- 04_01_SRTRewrite.py
S7_04_02_StoryBoard            <- 04_02_StoryBoard.py
S8_05_01_VideoPlanGenerator    <- 05_01_VideoPlanGenerator.py
```

`05_01` 的边界：

1. 只读 StoryBoard 结构和现有素材绑定。
2. 只生成视频生成计划。
3. 不调用 TTS。
4. 不调用图片模型。
5. 不调用视频模型。
6. 不拼接视频。
7. 不修改 `srt_storyboard.json` 中已有 Dialogue、Scene、Shot 结构。
8. 必须同时检查人物一致性 `HOST` 和产品一致性 `Product` 的最终结果图片是否存在；缺失只写入计划 JSON，不阻断计划生成。

## 3. 支持的目标范围

工具必须支持三种目标范围。

### 3.1 Scene 模式

输入指定：

```text
--target-type scene
--shot-id shot_001
--scene-id scene_002
```

行为：

1. 只规划该 Scene。
2. 如果该 Scene 不是全片第一个 Scene，且开头没有可用首帧，可以尝试引用上一个 Scene 的最后视频尾帧。
3. Scene 模式只规划当前 Scene；如果需要引用上一个 Scene 尾帧，该尾帧必须已经真实存在，不能引用本次计划中不存在的未来产物。
4. 如果当前 Scene 是全片第一个 Scene且没有可用视觉来源，则该 Scene 不生成，标记为 skipped。
5. 如果当前 Scene 不是全片第一个 Scene，开头没有可用视觉来源且上一个 Scene 尾帧不存在，则该 Scene blocked。

### 3.2 Shot 模式

输入指定：

```text
--target-type shot
--shot-id shot_001
```

行为：

1. 按 Shot 内 Scene 顺序逐个规划。
2. Scene 之间可以继承前一个 Scene 本轮计划生成的视频尾帧。
3. 如果 Shot 的第一个 Scene 没有可用首帧，可以尝试引用全局上一个 Scene 的最后视频尾帧。
4. 如果全局上一个 Scene 不存在或没有视频尾帧，则该 Scene blocked。
5. 一个 Scene skipped 或 blocked 不应阻止工具继续分析同 Shot 后续 Scene。
6. 后续 Scene 如果依赖 skipped / blocked Scene 的尾帧，也应 blocked；后续 Scene 如果自己有可用视觉来源，则继续规划为 planned。

### 3.3 Task / ShotPlan 模式

输入指定：

```text
--target-type task
```

含义：

```text
当前 workspace 的整个 SessionOutput/storyboard/srt_storyboard.json
```

这里的 `task` 不要求工具重新访问数据库，也不表示用 `task_id` 查询数据库；它表示本 Session 的 `SessionOutput/storyboard/srt_storyboard.json` 中当前 StoryBoard / ShotPlan 的全量范围。

行为：

1. 按 `shots[]` 顺序遍历。
2. 每个 Shot 内按 `scenes[]` 顺序遍历。
3. 每个 Scene 按 `dialogue_items[]` 顺序遍历。
4. 全片第一个 Scene 的第一个 Dialogue 必须有可用首帧来源。
5. 后续 Scene 可以继承上一个视频片段的尾帧。

## 4. 输入

必需输入：

```text
SessionContext/Variables.json
SessionOutput/storyboard/srt_storyboard.json
```

可选输入：

```text
SessionOutput/storyboard/Working/
SessionOutput/storyboard/assets/images/
SessionOutput/storyboard/assets/videos/
SessionOutput/storyboard/assets/audios/
SessionOutput/visual/srt_frames/
```

工具正式 run 阶段只读取自己的 Working 快照：

```text
S8_05_01_VideoPlanGenerator/Working/InputFrom_0_Variables.json
S8_05_01_VideoPlanGenerator/Working/InputFrom_7_srt_storyboard.json
S8_05_01_VideoPlanGenerator/Working/InputParams_video_generation_plan.json
```

prepare 阶段负责把上游文件复制到 Working。

本工具不创建 `Prompt/` 目录。第一版不调用模型，不生成 Prompt 审计文件。

## 5. 参数

建议参数：

```text
--workspace <workspace>
--target-type scene|shot|task
--shot-id <shot_id>
--scene-id <scene_id>
--max-video-seconds 4.0
--min-video-seconds 4.0
--split-tolerance-seconds 1.0
--force
--resume
--print-json
```

参数规则：

1. `--target-type scene` 必须提供 `--shot-id` 和 `--scene-id`。
2. `--target-type shot` 必须提供 `--shot-id`，不得要求 `--scene-id`。
3. `--target-type task` 不需要 `--shot-id` 或 `--scene-id`。
4. `--max-video-seconds` 控制单张图片覆盖过长 Scene 时的切段目标，默认 `4.0`。
5. `--min-video-seconds` 控制视频模型单次生成的最小时长，默认 `4.0`。
6. `--split-tolerance-seconds` 用于选择最接近目标时长的 Dialogue 边界，默认建议 `1.0`。

## 6. 核心概念

### 6.1 Dialogue

Dialogue 是素材和时间的最小稳定单位。

每个 Dialogue 至少需要：

```json
{
  "srt_id": "srt_0001_01",
  "dialogue": "给家里备这个化橘红啊",
  "start": 0.205,
  "end": 1.643,
  "duration": 1.438,
  "image_path": "SessionOutput/visual/srt_frames/srt_0001_01.jpg"
}
```

### 6.2 首帧来源

首帧来源枚举：

```text
original_image
generated_image
placed_uploaded_image
previous_segment_tail_frame
previous_scene_tail_frame
bound_video
missing
```

解释：

1. `original_image`：来自原视频参考帧，例如 `dialogue.image_path` 或 `key_frame_paths[]`。它只是生成新图片的参考，不直接作为视频首帧。
2. `generated_image`：来自已经生成并绑定到 Dialogue 的新图片。
3. `placed_uploaded_image`：来自上传素材或原素材，但已经被用户拖入 / 复制到 Dialogue 新图片槽位的图片。
4. `previous_segment_tail_frame`：来自当前 Scene 前一个视频片段尾帧。
5. `previous_scene_tail_frame`：来自前一个 Scene 最后视频尾帧。
6. `bound_video`：来自 Dialogue 已绑定的视频素材。它不是首帧图片，而是“该视频片段已有人为落位视频”的完成态视觉来源。
7. `missing`：没有可用首帧或已绑定视频，不能生成视频。

### 6.3 旧图片与新图片

旧图片：

```text
原视频参考帧 / 原图 / image_path / key_frame_paths[]
```

旧图片不能直接作为视频生成首帧使用；它只能作为生成新图片的参考锚点，触发：

```text
need_image_prompt = true
need_image = true
```

新图片：

```text
已经生成并落位到 Dialogue 新图片槽位的图片，或用户把上传图片复制 / 拖入到新图片落位后形成的图片
```

只有新图片可以直接作为视频首帧；它触发：

```text
need_image_prompt = false
need_image = false
```

上传图片本身不自动等同于新图片。只有上传图片被复制或拖入 Dialogue 的新图片落位后，才可以作为视频首帧。

如果新图片是从原素材或上传素材拖入形成的，计划必须记录后续视频生成工具需要执行的素材物化动作：

```json
{
  "materialize_first_frame": {
    "required": true,
    "copy_from_path": "SessionOutput/storyboard/assets/images/upload_001.png",
    "copy_to_path": "SessionOutput/storyboard/Working/srt_0001_01_Image_01.png",
    "source_type": "placed_uploaded_image"
  }
}
```

如果新图片已经在 `SessionOutput/storyboard/Working/` 中，则 `materialize_first_frame.required=false`。

### 6.4 已绑定视频

Dialogue 绑定的视频是独立于图片的 Segment anchor。`05_01` 必须把以下位置的视频视为当前 Dialogue 的视频来源：

```text
dialogue.working_assets.video.path
scene.working_assets.video.path    # 仅旧数据兼容；优先使用 dialogue 级
```

规则：

1. 绑定视频和原图 / 新图一样，都会开启一个新的 Segment。
2. 该 Segment 从绑定视频所在 Dialogue 开始，直到当前 Scene 内下一个带原图 / 新图 / 绑定视频的 Dialogue 之前结束。
3. Segment 不跨 Scene；下一个 Scene 必须重新规划自己的第一个 Segment。
4. 绑定视频路径必须真实存在且非空；如果 StoryBoard JSON 中有绑定路径但文件不存在，不能把它当作 `bound_video` 视觉来源，也不能标记为完成态。
5. 绑定视频文件不存在时，该 Dialogue / Segment 应进入明确 blocked 状态，blocked code 建议为 `bound_video_file_missing`，并保留可见 payload 供 UI 展示原因。
6. 绑定视频表示该 Segment 的视频步骤已经有人为落位结果，计划中必须标记为完成态：

```json
{
  "status": "ready",
  "first_frame": {
    "source_type": "bound_video",
    "source_path": "SessionOutput/storyboard/assets/videos/upload_001.mp4"
  },
  "tasks": {
    "need_video_prompt": false,
    "need_video": false,
    "need_lipsync": false
  },
  "existing_video": {
    "path": "SessionOutput/storyboard/assets/videos/upload_001.mp4",
    "materialize_video": {
      "required": true,
      "copy_from_path": "SessionOutput/storyboard/assets/videos/upload_001.mp4",
      "copy_to_path": "SessionOutput/storyboard/Working/srt_0005_01_Video_Final.mp4",
      "source_type": "bound_dialogue_video"
    }
  },
  "planned_outputs": {
    "video_path": "SessionOutput/storyboard/Working/srt_0005_01_Video_Final.mp4",
    "video_prompt_path": ""
  }
}
```

7. `05_01` 只写计划，不拷贝视频文件。`05_02` 执行时必须把绑定视频拷贝 / 物化到 `planned_outputs.video_path`，并使用和生成视频完全一致的命名规则：`{first_dialogue_asset_key}_Video_Final.mp4`。
8. 如果绑定视频已经在 `SessionOutput/storyboard/Working/` 中，但文件名不是该 Segment 的规范命名，`05_02` 仍需拷贝或重命名到规范路径；不能直接把非规范路径当作当前 Segment 完成产物。
9. 绑定视频完成的是视频生成步骤。Dialogue 音频仍按 `dialogue_audio_tasks[]` 独立判断；如果后续合成需要 Segment 级音频，仍由 `05_02` 根据 plan 处理。
10. 绑定视频 Segment 默认不执行 Sync，因为用户已经提供了该 Segment 的最终视频素材。是否允许后续空 Dialogue 继承它的尾帧，由该 Segment 首个 Dialogue 的 `video_plan.is_talking_head` 决定：`false` 表示空镜，尾帧不可继承；`true` 或字段缺失按兼容策略视为口播尾帧可继承。

## 7. Scene 规划规则

### 7.1 全片第一个 Scene

如果当前 Scene 是全片第一个 Scene：

1. 第一个 Dialogue 必须有 `srt_id`。
2. 第一个 Dialogue 必须有可触发首帧链路的视觉来源：
   - 已落位的新图片，可以直接作为视频首帧。
   - 旧图片 / 原图，可以先生成图片提示词和新图片，再用新图片作为视频首帧。
3. 如果第一个 Scene 没有可用视觉来源，则该 Scene 不生成视频，状态标记为 `skipped`，原因是 `first_scene_missing_visual_source`。
4. first Scene skipped 后，后续 Scene 仍继续遍历；后续 Scene 如果自己有可用视觉来源，可以继续 planned。

skip code：

```text
first_scene_missing_visual_source
```

### 7.2 非第一个 Scene 为空开头

如果当前 Scene 不是全片第一个 Scene，且第一个 Dialogue 没有已落位新图、绑定视频，也没有旧图可触发新图生成：

1. 优先使用当前范围内上一个视频片段尾帧。
2. 如果当前范围没有上一个视频片段，则尝试使用全局上一个 Scene 已生成视频尾帧。
3. 只有口播 Segment 的尾帧可以被后续空开头 Scene 继承。
4. 如果上一 Segment 的首个 Dialogue 标记了 `video_plan.is_talking_head=false`，该 Segment 是空镜；即使它有视频或尾帧，也不能作为后续空 Dialogue / 空 Scene 的生成起点。
5. 如果上一个 Scene 没有视频、尾帧不可用，或上一可用视频片段是空镜且当前 Scene 自己没有视觉来源，则当前 Scene blocked。

blocked code：

```text
scene_first_dialogue_missing_first_frame_and_previous_tail_missing
previous_segment_cutaway_tail_not_usable
```

### 7.3 多视觉来源 Scene

如果 Scene 中有多张图片或绑定视频，按视觉来源位置切段。视觉来源包括：

```text
原图 / 旧图片
新图 / 已落位图片
绑定视频
```

示例：

```text
dialogue 1 有图
dialogue 2 无图
dialogue 3 有视频
dialogue 4 无图
dialogue 5 无图
dialogue 6 有图
```

计划：

```text
segment 1: dialogue 1-2
segment 2: dialogue 3-5  # 绑定视频，视频步骤完成
segment 3: dialogue 6
```

每个视觉来源对应至少一个 Segment。图片来源通常触发视频生成任务；绑定视频来源触发视频物化任务，不触发视频生成任务。

如果图片是旧图片：

```text
need_image_prompt = true
need_image = true
need_video_prompt = true
need_video = true
```

其中旧图片只作为图片生成参考，实际视频首帧使用计划生成的新图片。

如果图片是新图片：

```text
need_image_prompt = false
need_image = false
need_video_prompt = true
need_video = true
```

如果视觉来源是绑定视频：

```text
need_image_prompt = false
need_image = false
need_video_prompt = false
need_video = false
need_lipsync = false
existing_video.materialize_video.required = true/false
planned_outputs.video_path = SessionOutput/storyboard/Working/{first_dialogue_asset_key}_Video_Final.mp4
```

如果某一张图片覆盖的 Dialogue 总时长超过 `max-video-seconds`，该图片段内部继续按 Dialogue 边界用尾帧切分。第一段使用该图片对应的新图片首帧，后续段使用前一段视频尾帧。

如果某个绑定视频覆盖的 Dialogue 总时长超过 `max-video-seconds`，不要为了模型限制拆分该绑定视频 Segment。它是用户已落位的视频资产，计划只记录它覆盖的 Dialogue 范围和规范 Working 输出路径；后续是否裁剪 / 复用由 `05_02` 或拼接工具按时间线处理。

如果某一个 Dialogue 自身时长已经超过 `max-video-seconds`，不得切断该 Dialogue。该 Dialogue 单独成为一个超长 segment，并标记：

```json
{
  "duration_exceeds_limit_unavoidable": true
}
```

如果每个 Dialogue 都有图片，即使每句很短，也按每张图片生成一个视频 segment。segment 的 `duration` 仍等于 Dialogue 原始时间轴时长，但 `planned_video_duration` 至少等于 `min-video-seconds`。短句视频允许超过原 Dialogue 时长，后续拼接 / 对齐工具再按时间轴裁剪或处理。

### 7.4 单图片长 Scene

如果整个 Scene 只有一张图片，但 Scene 时长超过 `max-video-seconds`，工具必须按 Dialogue 边界切成多个视频片段。

切段原则：

1. 不切断 Dialogue。
2. 在 Dialogue 结束点中选择最接近 `max-video-seconds` 的边界。
3. 后续片段的首帧来自前一个视频片段尾帧。
4. 后续片段不再生成图片提示词，也不再生成新图片。
5. 如果单条 Dialogue 时长超过 `max-video-seconds`，该 Dialogue 单独成段，不再继续切割。

示例：

```text
segment 1:
  first_frame_source = generated_image 或 original_image -> planned_generated_image
  need_audio = true
  need_image_prompt = true/false
  need_image = true/false
  need_video_prompt = true
  need_video = true

segment 2:
  first_frame_source = previous_segment_tail_frame
  need_audio = true
  need_image_prompt = false
  need_image = false
  need_video_prompt = true
  need_video = true
```

### 7.5 零图片 Scene

如果 Scene 内没有任何图片或绑定视频：

1. 如果存在上一个视频尾帧，则从上一个视频尾帧开始规划。
2. 只有口播 Segment 的尾帧可以继续用于零视觉来源 Scene。
3. 如果上一个视频片段是空镜，零视觉来源 Scene blocked，不允许用空镜尾帧继续生成口播或无锚点画面。
4. 如果不存在上一个视频尾帧，则 blocked。
5. 如果 Scene 时长超过 `max-video-seconds`，同样按 Dialogue 边界切段；切出的后续段也必须继续遵守“空镜尾帧不可继承”的规则。

零图片 Scene 的任务行：

```text
need_audio = true
need_image_prompt = false
need_image = false
need_video_prompt = true
need_video = true
first_frame_source = previous_scene_tail_frame 或 previous_segment_tail_frame
```

### 7.6 Dialogue 音频规划

每个被 segment 覆盖的 Dialogue 都需要音频。

如果 Dialogue 已经有可用最终音频：

```text
need_audio = false
audio_source = existing_dialogue_audio
```

如果 Dialogue 没有可用最终音频：

```text
need_audio = true
audio_source = to_generate
```

segment 级 `need_audio` 建议表示：

```text
该 segment 中是否至少有一个 Dialogue 需要生成音频
```

同时在 `dialogue_audio_tasks[]` 中记录每个 Dialogue 的音频状态。

## 8. Shot / Task 遍历规则

### 8.1 Shot 模式遍历

Shot 模式按当前 Shot 内 Scene 顺序执行 Scene 规划。

规则：

1. 上一个 Scene 的最后 segment 如果计划生成视频，则当前 Scene 可以引用其计划尾帧。
2. 如果上一个 Scene 已 skipped 或 blocked 且没有尾帧，当前空开头 Scene 也 blocked。
3. 如果当前 Scene 自己有图片，则不依赖前一个 Scene。
4. Shot 模式输出中必须保留 Scene 级分组，便于 UI 展开。

### 8.2 Task / ShotPlan 模式遍历

Task 模式遍历整个 `srt_storyboard.json`。

规则：

1. `shots[]` 顺序必须保持。
2. `scenes[]` 顺序必须保持。
3. `dialogue_items[]` 顺序必须保持。
4. 每个 Dialogue 最多出现在一个 segment 中。
5. 每个 Scene 可以有 0 个、1 个或多个 segment。
6. 如果某个 Scene skipped 或 blocked，必须记录原因，不得静默跳过。
7. skipped / blocked Scene 不阻断后续自带可用视觉来源的 Scene。
8. skipped / blocked Scene 也必须输出可见但不可执行的 segment payload，至少包含 `segment_id`、`asset_key`、`dialogue_ids[]`、`dialogue_audio_tasks[]`、`tasks.need_* = false`、`planned_outputs` 空值和 `blocked_reason` / `skipped_reason`。前端必须能展示阻塞原因和音频任务，不能因为没有可执行 segment 就让 Scene 在 UI 中消失。

## 9. 输出

最终业务输出：

```text
SessionOutput/storyboard/video_generation_plan.json
```

工具 Output 快照：

```text
S8_05_01_VideoPlanGenerator/Output/video_generation_plan.json
```

工具执行报告：

```text
S8_05_01_VideoPlanGenerator/Report/Result.json
```

Working 快照：

```text
S8_05_01_VideoPlanGenerator/Working/InputFrom_0_Variables.json
S8_05_01_VideoPlanGenerator/Working/InputFrom_7_srt_storyboard.json
S8_05_01_VideoPlanGenerator/Working/InputParams_video_generation_plan.json
S8_05_01_VideoPlanGenerator/Working/State_progress.json
```

## 10. video_generation_plan.json Schema

建议结构：

```json
{
  "schema_version": "analysis_v1_video_generation_plan_0.1",
  "tool": "05_01_VideoPlanGenerator",
  "tool_version": "0.1.0",
  "source_storyboard_path": "SessionOutput/storyboard/srt_storyboard.json",
  "target": {
    "target_type": "scene",
    "shot_id": "shot_001",
    "scene_id": "scene_001"
  },
  "settings": {
    "max_video_seconds": 4.0,
    "min_video_seconds": 4.0,
    "split_tolerance_seconds": 1.0
  },
  "consistency_references": {
    "status": "missing_reference_images",
    "blocking": false,
    "references": [
      {
        "kind": "host",
        "label": "人物一致性",
        "available": false,
        "output_path": "",
        "expected_final_image_paths": [
          "SessionContext/Consistency/HOST.png",
          "SessionContext/Consistency/HOST.jpg",
          "SessionContext/Consistency/HOST.jpeg",
          "SessionContext/Consistency/HOST.webp"
        ],
        "missing_reason": "final_reference_image_missing"
      },
      {
        "kind": "product",
        "label": "产品一致性",
        "available": false,
        "output_path": "",
        "expected_final_image_paths": [
          "SessionContext/Consistency/Product.png",
          "SessionContext/Consistency/Product.jpg",
          "SessionContext/Consistency/Product.jpeg",
          "SessionContext/Consistency/Product.webp"
        ],
        "missing_reason": "final_reference_image_missing"
      }
    ],
    "missing": [
      {
        "kind": "host",
        "label": "人物一致性",
        "code": "host_consistency_reference_image_missing",
        "message": "缺少人物一致性最终结果图片，不阻碍 video plan 生成。"
      },
      {
        "kind": "product",
        "label": "产品一致性",
        "code": "product_consistency_reference_image_missing",
        "message": "缺少产品一致性最终结果图片，不阻碍 video plan 生成。"
      }
    ]
  },
  "summary": {
    "shot_count": 1,
    "scene_count": 1,
    "dialogue_count": 5,
    "segment_count": 2,
    "skipped_scene_count": 0,
    "blocked_scene_count": 0,
    "need_audio_count": 5,
    "segment_audio_count": 2,
    "need_image_prompt_count": 1,
    "need_image_count": 1,
    "need_video_prompt_count": 2,
    "need_video_count": 2,
    "need_lipsync_count": 2
  },
  "shots": [
    {
      "shot_id": "shot_001",
      "status": "planned",
      "scenes": [
        {
          "scene_id": "scene_001",
          "status": "planned",
          "start": 0.205,
          "end": 6.68,
          "duration": 6.475,
          "segments": [
            {
              "segment_id": "shot_001_scene_001_segment_001",
              "segment_index": 1,
              "start": 0.205,
              "end": 4.582,
              "duration": 4.377,
              "planned_video_duration": 4.377,
              "duration_padding_seconds": 0.0,
              "duration_policy": "match_dialogue_timeline",
              "duration_exceeds_limit_unavoidable": false,
              "dialogue_ids": ["srt_0001_01", "srt_0001_02", "srt_0002_01"],
              "dependencies": {
                "depends_on_segment_id": "",
                "depends_on_video_path": "",
                "depends_on_tail_frame_path": ""
              },
              "first_frame": {
                "source_type": "original_image",
                "source_path": "SessionOutput/visual/srt_frames/srt_0001_01.jpg",
                "requires_generated_image_before_video": true,
                "planned_generated_image_path": "SessionOutput/storyboard/Working/srt_0001_01_Image_01.png",
                "materialize_first_frame": {
                  "required": false,
                  "copy_from_path": "",
                  "copy_to_path": "",
                  "source_type": ""
                }
              },
              "tail_frame": {
                "planned_path": "SessionOutput/storyboard/Working/srt_0001_01_TailFrame.png",
                "available": false,
                "continuation_allowed": true,
                "continuation_policy_source": "dialogue.video_plan.is_talking_head"
              },
              "tasks": {
                "need_audio": true,
                "need_image_prompt": true,
                "need_image": true,
                "need_video_prompt": true,
                "need_video": true,
                "need_lipsync": true,
                "lipsync_disabled_by_ui": false,
                "lipsync_reason": "visible_face",
                "lipsync_decision_source": "default"
              },
              "existing_video": {
                "path": "",
                "materialize_video": {
                  "required": false,
                  "copy_from_path": "",
                  "copy_to_path": "",
                  "source_type": ""
                }
              },
              "dialogue_audio_tasks": [
                {
                  "srt_id": "srt_0001_01",
                  "need_audio": true,
                  "planned_audio_path": "SessionOutput/storyboard/Working/srt_0001_01_Audio_Final.wav"
                }
              ],
              "planned_outputs": {
                "image_prompt_path": "SessionOutput/storyboard/Working/srt_0001_01_ImagePrompt.json",
                "image_path": "SessionOutput/storyboard/Working/srt_0001_01_Image_01.png",
                "segment_audio_path": "SessionOutput/storyboard/Working/srt_0001_01_SegmentAudio_Final.wav",
                "video_prompt_path": "SessionOutput/storyboard/Working/srt_0001_01_VideoPrompt.json",
                "video_path": "SessionOutput/storyboard/Working/srt_0001_01_Video_Final.mp4",
                "video_duration_seconds": 4.377
              },
              "blocked_reason": ""
            }
          ],
          "blocked_reason": ""
        }
      ]
    }
  ],
  "created_at": "2026-05-30T00:00:00Z"
}
```

绑定视频 Segment 的同一 Schema 必须满足：

1. `first_frame.source_type="bound_video"`。
2. `existing_video.path` 记录 StoryBoard 中绑定的视频路径。
3. `existing_video.materialize_video.copy_to_path` 等于 `planned_outputs.video_path`。
4. `planned_outputs.video_path` 使用该 Segment 第一个 Dialogue 的稳定 key 命名。
5. `planned_outputs.video_prompt_path=""`，避免 05_02 误以为需要生成提示词。
6. `tail_frame.continuation_allowed=false` 仅在该 Segment 首个 Dialogue 明确 `video_plan.is_talking_head=false` 时成立；该字段缺失时按兼容策略视为 `true`。

### 10.1 一致性参考检查与前端缓存契约

`05_01` 顶层必须输出 `consistency_references`，它是计划输入状态的一部分，不是纯展示字段。前端 Generation Plan 是否可以复用缓存，必须把该字段对应的当前文件状态算入判断。

必须检查两类最终一致性参考：

| kind | 含义 | 标准最终文件候选 |
| --- | --- | --- |
| `host` | 人物一致性参考 | `SessionContext/Consistency/HOST.png`、`HOST.jpg`、`HOST.jpeg`、`HOST.webp` |
| `product` | 产品一致性参考 | `SessionContext/Consistency/Product.png`、`Product.jpg`、`Product.jpeg`、`Product.webp` |

检查规则：

1. `HOST` 和 `Product` 必须逐项独立检查，不能只检查其中一个。
2. 如果标准最终文件不存在，可以兼容读取对应 manifest 中的 `output`，但 manifest 的 `output` 必须指向 workspace 内真实存在且非空的文件。
3. manifest 中只有 `uploaded_output_filename`、`uploaded_at` 或历史上传记录，不能视为最终参考已存在。
4. `consistency_config.json.active.host_reference` 和 `active.product_reference` 只能作为 UI 当前选择状态参考，不能替代真实文件存在性检查。
5. 任一项缺失时，`consistency_references.status` 必须为 `missing_reference_images`，对应项 `available=false`，`output_path=""`，并在 `missing[]` 中记录 `host_consistency_reference_image_missing` 或 `product_consistency_reference_image_missing`。
6. 缺失一致性参考不阻断 `05_01` 生成，`blocking=false`；但前端必须给用户可见提示。

前端缓存契约：

1. `video_generation_plan.ui_cache.json` 的签名必须包含当前 `HOST` 和 `Product` 的可用状态与 `output_path`。
2. 打开 Generation Plan 时，命中缓存前必须再次读取当前文件状态，并和旧 plan 的 `consistency_references.references[]` 逐项对比。
3. 如果 `host` 或 `product` 任意一项的 `available` 或 `output_path` 和当前状态不一致，必须判定缓存失效并重新调用 `05_01`。
4. 旧 plan 里显示 `status=ready` 但当前文件已删除，是典型脏缓存；前端不能继续展示旧 plan。
5. 旧 `ui_cache` 没有 `consistency_reference_signature` 时，必须视为不可信缓存，重新生成。
6. 重新生成后，Modal 顶部或全局提示区必须显示缺失项，例如“缺少产品一致性最终结果图片，不阻碍 video plan 生成。”
7. `video_generation_plan.ui_cache.json` 的签名还必须包含当前 StoryBoard 媒体绑定状态，至少覆盖 `working_assets.images[]`、`working_assets.video`、`bound_image_path`、`bound_video_path` 或等价字段。用户在主界面删除新图/视频素材并保存后，下一次打开 Generation Plan 必须判定旧 plan 失效，而不是继续复用旧 plan。
8. 前端调用重新生成计划时传入的 `force=true` 必须被后端执行入口真正尊重。即使结构签名看起来一致，只要用户明确触发 force，本次也必须绕过 `ui_cache`，重新调用 `05_01` 并写入新的 `video_generation_plan.json` / `video_generation_plan.ui_cache.json`。
9. 因 StoryBoard 媒体绑定变化而重跑 `05_01` 时，不能把旧 `05_02` 执行状态直接套到新 plan 上。重置计划输出时必须同步清理或隔离 `video_plan_execution_state.json`、`video_plan_execution_result.json` 和 `S9_05_02_VideoPlanExecutor/`，除非用户明确选择保留历史执行内容；否则旧的 completed_working 会把新 plan 的 First Frame / Video / Sync 误显示为绿色。
10. Generation Plan 的展示状态必须同时校验 StoryBoard Working 中真实文件是否存在、StoryBoard JSON 是否仍绑定该文件，以及 execution state/result 是否属于当前 plan。只匹配 plan hash 或只读旧 execution state，都不足以证明当前素材仍有效。

本次前端交付踩坑记录：

1. 曾只看到人物参考缺失，忽略产品参考缺失；最终规则必须写成 `HOST` 和 `Product` 双检查。
2. 曾出现 `Product.png` 已删除、`product_manifest.json.output=""`，但旧 plan 仍显示 Product 可用；原因是旧 plan / ui cache 没有被当前文件状态强制失效。
3. 只修 `05_01` 输出不够。因为用户打开的是前端 Modal，前端缓存命中会绕过工具重跑，所以必须同时修缓存签名和“缓存返回前的 plan-current-state 对比”。
4. 只在 JSON 中记录缺失也不够。Modal 必须把缺失项展示出来，否则用户会误以为参考图仍然生效。
5. 前端构建通过不代表运行中的本地服务已经加载新代码；如果 UI 仍旧展示旧状态，优先检查后端/前端服务是否重启，以及当前返回的 `video_generation_plan.json` 与 `ui_cache` 是否为新版本。
6. 当前设计允许缺失一致性参考继续出 plan；这是一条 warning，不是 blocked，也不应该清空其它可规划任务。
7. Task #33 / Session #89 已经出现过：用户在主界面清空图片/视频槽位后，`koubo_storyboard_edit.json` 实际已经为空，但后端仍复用旧 `video_generation_plan.json` 和旧 `video_plan_execution_state.json`，导致 Modal 继续展示上一轮绿色完成状态。根因是 force 未进入后端缓存判定，且 reset 只清了 `05_01` 计划文件，没有清旧 `05_02` 执行状态。
8. 同一轮还出现过：旧 execution state 被清掉后，StoryBoard Working 里仍有最终视频，Generation Plan 能推断 Video 绿色，但 Sync 变成白色/黄色。非对嘴型 `audio_replace_retime` 段可以从“最终视频存在 + segment audio 存在 + plan 不需要 lipsync”推断 Sync 完成；对嘴型段不能这样推断，必须依赖本轮明确的 Sync execution state/result。
9. 删除素材后的正确行为不是只刷新 Modal 的前端本地状态，而是重新生成 plan、重置旧执行状态、重新计算 artifact status 三件事同步成立。任何一环缺失，都会出现主界面已空但 Generation Plan 仍绿、或主界面已有视频但 Sync 状态发白的错位。

### 10.2 Dialogue 级口播/空镜标记输入

`05_01` 规划 Sync 时，必须优先读取 Dialogue 上的人工口播标记。这个标记由 VideoPlan Modal 的 Sync 右键菜单写回 StoryBoard Dialogue，不由 `05_01` 自己生成。

推荐 Dialogue 字段：

```json
{
  "video_plan": {
    "is_talking_head": false,
    "lipsync_override": "skip_cutaway",
    "lipsync_override_source": "ui_sync_context_menu",
    "lipsync_override_reason": "user_marked_cutaway",
    "lipsync_override_updated_at": "2026-05-31T00:00:00Z"
  }
}
```

规划规则：

1. 如果图片 / 尾帧驱动生成的 Segment 首个 Dialogue 存在 `video_plan.is_talking_head=false`，该 Segment 不规划 Sync：

```json
{
  "tasks": {
    "need_lipsync": false,
    "lipsync_disabled_by_ui": true,
    "lipsync_reason": "user_marked_cutaway",
    "lipsync_decision_source": "dialogue.video_plan.is_talking_head"
  }
}
```

2. 如果图片 / 尾帧驱动生成的 Segment 存在 `video_plan.is_talking_head=true`，继续规划 Sync：

```json
{
  "tasks": {
    "need_lipsync": true,
    "lipsync_disabled_by_ui": false,
    "lipsync_reason": "dialogue_marked_talking_head",
    "lipsync_decision_source": "dialogue.video_plan.is_talking_head"
  }
}
```

3. 如果图片 / 尾帧驱动生成的 Segment 字段缺失、为 `null`、旧 StoryBoard 没有该结构，或当前 Segment 找不到可写回的 Dialogue，按兼容策略默认执行 Sync：

```json
{
  "tasks": {
    "need_lipsync": true,
    "lipsync_disabled_by_ui": false,
    "lipsync_reason": "default_execute_when_dialogue_flag_missing",
    "lipsync_decision_source": "missing_dialogue_flag_default"
  }
}
```

4. 如果 Segment 来源是绑定视频，`05_01` 不规划 Sync，原因记录为 `existing_video_bound_complete`。该 Segment 是否允许后续空 Dialogue 使用尾帧，仍读取同一个 `video_plan.is_talking_head` 字段：`false` 表示空镜尾帧不可继承，`true` 或缺失表示按口播尾帧兼容处理。
5. 如果一个 Segment 覆盖多个 Dialogue，第一版优先以该 Segment 中“有图片 / First Frame 来源 / 绑定视频”的第一个 Dialogue 为准；如果没有任何 Dialogue 带视觉来源，则以该 Segment 的第一个 Dialogue 为准。后续如果扩展为多 Dialogue 联合判断，仍必须以 Dialogue 字段为输入，不能以 plan 或 ui cache 为唯一事实来源。
6. `05_01` 只读取该字段并写入 plan，不反向修改 `srt_storyboard.json`。
7. 该字段变化应纳入前端 VideoPlan 缓存的 StoryBoard structure signature 或 media/plan signature；用户设置空镜或恢复口播后，前端必须重新调用 `05_01` 刷新执行计划，不能只更新当前 Modal 的本地状态。
8. 每次输出 plan 时必须提供可绑定后续执行结果的 `plan_hash` 或 `plan_run_id`；`05_02` execution result 必须记录同一个标识，前端只允许把标识匹配的执行结果绑定到当前 plan。

## 11. 命名规则

Dialogue 级素材继续使用稳定 Dialogue key：

```text
{dialogue_asset_key}_Audio_Final.wav
{dialogue_asset_key}_Image_01.png
```

视频片段优先落位到该视频片段覆盖范围内的第一个 Dialogue，使用该 Dialogue 的稳定 key 命名：

```text
{first_dialogue_asset_key}_Video_Final.mp4
{first_dialogue_asset_key}_VideoPrompt.json
{first_dialogue_asset_key}_TailFrame.png
```

原因：

1. UI 上视频优先匹配并落位到该视频的第一个 Dialogue。
2. Dialogue key 比 Scene / segment 序号更稳定，重排 Scene 后更容易追踪素材。
3. 一个 Dialogue 只会作为一个视频片段的起点，因此可以作为该视频片段的素材落位身份。
4. segment 仍保留 `segment_id`、`segment_index` 和 `dialogue_ids[]`，用于拼接顺序和计划审计。

## 12. Result.json 合同

`Report/Result.json` 至少包含：

```json
{
  "tool": "05_01_VideoPlanGenerator",
  "tool_version": "0.1.0",
  "status": "completed",
  "workspace_dir": "",
  "requires_database": false,
  "requires_model_calls": false,
  "inputs": {
    "variables": "S8_05_01_VideoPlanGenerator/Working/InputFrom_0_Variables.json",
    "storyboard": "S8_05_01_VideoPlanGenerator/Working/InputFrom_7_srt_storyboard.json",
    "params": "S8_05_01_VideoPlanGenerator/Working/InputParams_video_generation_plan.json"
  },
  "outputs": {
    "tool_output": "S8_05_01_VideoPlanGenerator/Output/video_generation_plan.json",
    "session_output": "SessionOutput/storyboard/video_generation_plan.json"
  },
  "summary": {
    "target_type": "scene",
    "shot_count": 1,
    "scene_count": 1,
    "segment_count": 2,
    "blocked_scene_count": 0
  },
  "blocked_reasons": [],
  "created_at": ""
}
```

如果存在局部 blocked Scene，但工具仍成功产出整体计划，工具状态建议仍为：

```text
completed_with_blocked_items
```

如果只存在 skipped Scene，且没有 blocked Scene，工具状态为：

```text
completed_with_skipped_items
```

如果同时存在 skipped 和 blocked Scene，工具状态为：

```text
completed_with_blocked_items
```

如果目标范围本身完全无法规划，例如指定 Scene 不存在，则工具状态为：

```text
blocked
```

## 13. 阻塞条件

工具级 blocked：

1. `SessionContext/Variables.json` 缺失。
2. `SessionOutput/storyboard/srt_storyboard.json` 缺失。
3. `srt_storyboard.json` 不是有效 JSON。
4. `shots[]` 为空。
5. `--target-type scene` 但缺少 `--shot-id` 或 `--scene-id`。
6. `--target-type shot` 但缺少 `--shot-id`。
7. 指定 `shot_id` 不存在。
8. 指定 `scene_id` 不存在。
9. 目标范围内没有 Dialogue。
10. `max-video-seconds <= 0`。

Scene 级 blocked：

1. 非第一个 Scene 空开头，且上一个 Scene / segment 没有可用视频尾帧。
2. Scene 模式运行时，当前 Scene 空开头，需要引用上一个 Scene 尾帧，但该尾帧文件不真实存在。
3. Scene 内 Dialogue 缺少 `srt_id` 且无法生成稳定 `dialogue_id`。
4. Scene 时间字段缺失且无法从 Dialogue 回填。
5. Scene 内 Dialogue 时间顺序不合法。
6. Scene 空开头或零视觉来源，且唯一可继承的上一个 Segment 是 `video_plan.is_talking_head=false` 的空镜 Segment。
7. Scene 的唯一视觉来源是绑定视频，但绑定视频路径不存在或文件为空。

Scene 级 skipped：

1. 全片第一个 Scene 没有已落位新图片，也没有可用于生成新图片的旧图片 / 原图。

Segment 级 blocked：

1. segment 没有任何 Dialogue。
2. segment 没有可用首帧。
3. 需要引用 `previous_segment_tail_frame`，但前一个 segment 不会生成视频或已 blocked。
4. 需要引用 `previous_segment_tail_frame`，但前一个 segment 是空镜，`tail_frame.continuation_allowed=false`。
5. segment 需要使用绑定视频作为视觉来源，但 `existing_video.path` 对应文件不存在或为空。

## 14. 验收标准

1. Scene 模式能只输出指定 Scene 的计划。
2. Shot 模式能按顺序输出该 Shot 内每个 Scene 的计划。
3. Task 模式能按顺序输出整个 StoryBoard 每个 Scene 的计划。
4. 多图片 Scene 能按图片切出多个 segment。
5. 单图片长 Scene 能按 Dialogue 边界切出多个 segment。
6. 零图片非首 Scene 能继承上一个视频尾帧。
7. 全片首 Scene 缺视觉来源时必须 skipped，不生成视频。
8. 输出中每个 Dialogue 最多出现一次。
9. 输出中每个 segment 的 `start/end/duration` 由 Dialogue 时间回填。
10. 输出中每个任务行都有任务开关：音频、图片提示词、图片、视频提示词、视频、对嘴型。
11. `Result.json` 不包含密钥、数据库 URL、cookie 或 auth header。
12. `--print-json` 输出结构与 `Report/Result.json` 一致。
13. 单条 Dialogue 超过 `max-video-seconds` 时不得切割，必须标记 `duration_exceeds_limit_unavoidable=true`。
14. 依赖上一段尾帧的 segment 必须写明 `depends_on_segment_id`、`depends_on_video_path` 和 `depends_on_tail_frame_path`。
15. 从原素材 / 上传素材拖入的新图片必须写明 `materialize_first_frame.copy_from_path` 和 `copy_to_path`。
16. 短句视频的 `planned_video_duration` 必须至少等于 `min-video-seconds`。
17. 本工具不得创建 `Prompt/` 目录。
18. 每个 video segment 必须包含 `need_lipsync`、`lipsync_disabled_by_ui`、`lipsync_reason`。
19. 前端明确关闭对嘴型时，计划必须写入 `need_lipsync=false` 和 `lipsync_disabled_by_ui=true`。
20. 每个 video segment 必须规划 `planned_outputs.segment_audio_path`，供 `05_02` 合成 segment 级音频并进入 StoryBoard Working。
21. 缺少人物一致性或产品一致性最终结果图片时，`consistency_references.missing[]` 必须记录缺失项，且工具状态不得因此 blocked。
22. 人物一致性 `HOST` 和产品一致性 `Product` 必须分别检查；不能因为 `HOST` 存在就默认 `Product` 存在，也不能反向推断。
23. 当 `Product.png` 不存在且 `product_manifest.json.output=""` 时，计划必须记录 `product_consistency_reference_image_missing`。
24. 当旧 plan 中 `Product.available=true` 但当前文件已不存在时，前端 VideoPlan 缓存必须失效并重新调用 `05_01`。
25. Generation Plan Modal 必须可见展示缺失的一致性参考项，不能只依赖用户打开 JSON 排查。
26. Dialogue 绑定视频必须作为 Scene 内新的 Segment 起点，直到遇到下一个原图 / 新图 / 绑定视频 Dialogue。
27. 绑定视频 Segment 的视频步骤必须是完成态：`need_video_prompt=false`、`need_video=false`、`planned_outputs.video_path` 为规范 Working 路径。
28. 绑定视频 Segment 必须记录 `existing_video.materialize_video.copy_from_path` 和 `copy_to_path`，供 `05_02` 拷贝到规范命名。
29. Segment 不跨 Scene；Scene 之间只允许通过可继承尾帧建立依赖。
30. 空镜 Segment 的尾帧不可用于后续空 Dialogue / 空 Scene；只有口播 Segment 的尾帧可以连续生成。
31. 绑定视频路径不存在时，不能输出 `first_frame.source_type="bound_video"` 的 ready segment，必须输出 blocked 可见 payload。
32. blocked / skipped Scene 必须至少包含一个可见但不可执行 segment payload，供 UI 展示阻塞原因和 `dialogue_audio_tasks[]`。
33. Task 模式必须覆盖跨 Shot 的计划尾帧延续；上游 Final 缺少 `TailFrame.png` 时下游 blocked；空镜 TailFrame 不可继承。

## 15. 测试计划

### 15.1 单元测试

建议测试文件：

```text
tests/analysis_v1/test_05_01_video_generation_plan.py
```

测试用例：

1. `test_scene_target_requires_shot_and_scene_id`
2. `test_shot_target_requires_shot_id`
3. `test_task_target_accepts_entire_storyboard`
4. `test_blocked_when_variables_missing`
5. `test_blocked_when_storyboard_missing`
6. `test_blocked_when_target_scene_not_found`
7. `test_first_scene_without_visual_source_is_skipped`
8. `test_original_image_requires_image_prompt_and_image_before_video`
9. `test_generated_image_slot_can_be_first_frame`
10. `test_unplaced_upload_asset_cannot_be_first_frame`
11. `test_multi_image_scene_splits_by_image_anchor`
12. `test_multi_image_overlong_anchor_range_splits_by_tail_frame`
13. `test_single_image_long_scene_splits_near_max_dialogue_boundary`
14. `test_single_overlong_dialogue_is_not_split`
15. `test_zero_image_non_first_scene_uses_previous_scene_tail`
16. `test_zero_image_non_first_scene_blocks_without_previous_tail`
17. `test_scene_scope_requires_existing_previous_tail_file`
18. `test_shot_scope_carries_tail_frame_between_scenes`
19. `test_task_scope_carries_tail_frame_across_shots`
20. `test_blocked_scene_does_not_drop_following_scenes`
21. `test_skipped_scene_does_not_drop_following_visual_scene`
22. `test_dialogue_audio_tasks_mark_existing_audio_as_not_needed`
23. `test_video_outputs_use_first_dialogue_key`
24. `test_tail_frame_dependencies_are_explicit`
25. `test_placed_uploaded_image_records_materialize_copy_action`
26. `test_each_short_dialogue_with_image_uses_model_minimum_video_duration`
27. `test_each_dialogue_appears_once_per_plan`
28. `test_segment_times_are_backfilled_from_dialogues`
29. `test_no_prompt_directory_created`
30. `test_result_json_has_no_secret_strings`
31. `test_print_json_matches_result_json`
32. `test_missing_host_consistency_reference_is_recorded_but_non_blocking`
33. `test_missing_product_consistency_reference_is_recorded_but_non_blocking`
34. `test_host_and_product_consistency_references_are_checked_independently`
35. `test_frontend_video_plan_cache_invalidates_when_consistency_reference_state_changes`
36. `test_bound_video_dialogue_starts_new_segment`
37. `test_bound_video_segment_marks_video_step_ready_and_records_materialize_copy`
38. `test_bound_video_uses_first_dialogue_key_for_working_video_path`
39. `test_segment_does_not_cross_scene_when_bound_video_appears_near_scene_end`
40. `test_cutaway_tail_blocks_following_empty_dialogue_without_visual_anchor`
41. `test_talking_head_tail_allows_following_empty_dialogue_continuation`

### 15.2 Fixture 设计

需要构造最小 StoryBoard fixtures：

1. `storyboard_first_scene_with_image.json`
2. `storyboard_first_scene_without_image.json`
3. `storyboard_multi_image_scene.json`
4. `storyboard_single_image_long_scene.json`
5. `storyboard_zero_image_non_first_scene_with_previous_video.json`
6. `storyboard_zero_image_non_first_scene_without_previous_video.json`
7. `storyboard_two_shots_cross_shot_tail.json`
8. `storyboard_first_scene_without_visual_source_then_later_visual_scene.json`
9. `storyboard_single_overlong_dialogue.json`
10. `storyboard_placed_uploaded_image_requires_materialize_copy.json`
11. `storyboard_bound_video_anchor.json`
12. `storyboard_cutaway_tail_then_empty_dialogue.json`
13. `storyboard_talking_head_tail_then_empty_dialogue.json`

每个 fixture 应尽量小，避免依赖真实媒体文件。路径可以是 workspace 相对路径，测试只验证规划逻辑。

### 15.3 集成测试

集成测试使用一个真实或近真实 workspace：

```text
SessionContext/Variables.json
SessionOutput/storyboard/srt_storyboard.json
```

覆盖：

1. Scene 模式规划 `shot_001 / scene_001`。
2. Shot 模式规划 `shot_001`。
3. Task 模式规划完整 StoryBoard。
4. `--max-video-seconds 4` 和 `--max-video-seconds 8` 输出 segment 数差异合理，但单条超长 Dialogue 不被切割。
5. 输出写入 `S8_05_01_VideoPlanGenerator/Output/` 和 `SessionOutput/storyboard/`。
6. 运行后确认没有创建 `S8_05_01_VideoPlanGenerator/Prompt/`。
7. 删除 `HOST.png` 后打开 Generation Plan，确认重新生成并提示人物一致性缺失。
8. 删除 `Product.png` 或清空 `product_manifest.json.output` 后打开 Generation Plan，确认重新生成并提示产品一致性缺失。

### 15.4 回归测试

每次 StoryBoard 结构调整后，需要回归：

1. Split Scene 后重新规划。
2. Merge Scene 后重新规划。
3. Move Dialogue 后重新规划。
4. 删除 Dialogue 图片后重新规划。
5. 添加 Dialogue 新图片后重新规划。
6. 清空某 Scene 视频后重新规划。
7. 给 Dialogue 绑定视频后重新规划。
8. 将绑定视频 Dialogue 标记为空镜后重新规划。

### 15.5 手工验收场景

建议人工查看 `video_generation_plan.json`：

1. 每个 Scene 展开后能直接看到几个视频片段。
2. 每个视频片段能看出首帧来自哪里。
3. 每个任务开关能直接指导后续工具。
4. blocked 原因能指导用户补图、补视频或先生成上一 Scene。
5. Generation Plan Modal 能直接展示 `HOST` 或 `Product` 缺失提示；如果删除文件后 UI 仍显示旧状态，必须检查服务是否已加载新代码以及 `video_generation_plan.ui_cache.json` 是否被正确失效。

## 16. 已确认问题

### 16.1 业务问题确认结果

| 问题 | 已确认答案 |
|---|---|
| 工具文件名 | 使用 `05_01_VideoPlanGenerator.py`。 |
| Step 目录 | 当前 Analysis_V1 链路使用 `S8_05_01_VideoPlanGenerator/`。 |
| `--target-type task` 含义 | 表示本 Session 的 `SessionOutput/storyboard/srt_storyboard.json` 中整个 ShotPlan，不查询数据库。 |
| 默认切段时长 | `max-video-seconds = 4.0`。 |
| 视频模型最短生成时长 | `min-video-seconds = 4.0`；短句视频允许超过原 Dialogue 时长。 |
| 多图片 Scene 中单个图片区段超时 | 继续按 Dialogue 边界用尾帧切分。 |
| 上传图片是否可做首帧 | 只有落位到新图片槽位的图片才能做首帧。 |
| 旧图片是否可做首帧 | 旧图片不能直接做首帧，除非被复制或拖到新图片落位；否则只能作为生成新图片的参考。 |
| 视频文件命名和落位 | 视频优先匹配该视频第一个 Dialogue 的位置落位，使用该 Dialogue key 命名。 |
| Dialogue 已绑定视频 | 该 Dialogue 作为新的 Segment 起点；该 Segment 视频步骤完成，`05_02` 只把绑定视频拷贝到规范 Working 视频路径。 |
| 尾帧文件 | 由后续视频生成工具产生并写回；`05_01` 只规划 `planned_path`。 |
| 空镜尾帧继承 | 空镜 Segment 的尾帧不可用于后续空 Dialogue / 空 Scene；只有口播 Segment 的尾帧可以连续生成。 |
| 局部 blocked 状态 | 使用 `completed_with_blocked_items`，继续向后，先完成能完成的。 |
| Shot 模式首 Scene 空开头 | 允许引用 Shot 外上一个 Scene 的已生成视频尾帧。 |
| Task 模式 blocked 后续处理 | blocked Scene 后面如果有自己图片的 Scene，可以继续规划为可执行。 |
| 音频任务粒度 | 按 Dialogue 粒度规划，segment 级 `need_audio` 只做汇总。 |
| Segment 合成音频 | 每个 video segment 必须规划 `planned_outputs.segment_audio_path`，由 `05_02` 按 Dialogue 顺序合成并写入 StoryBoard Working。 |
| 输出写回策略 | 独立产出新的 JSON，每次运行覆盖该 JSON，不写回 `srt_storyboard.json`。 |
| 人类可读报告 | 不在本工具里生成；报告由单独报告工具或界面生成。 |
| 首个 Scene 无图 | 第一个 Scene 如果没有视觉来源就不生成，标记为 skipped。 |
| 单条 Dialogue 超过 4 秒 | 不切割，单独成段并标记不可避免超长。 |
| 尾帧依赖 | 必须显式写入依赖的 segment、video 和 tail frame path。 |
| Scene 单独运行引用上一 Scene | 只能引用真实已存在的上一 Scene 尾帧文件。 |
| blocked / skipped 后续传播 | 依赖 skipped / blocked 尾帧的后续 Scene blocked；后续有自己视觉来源的 Scene 继续 planned。 |
| 从原素材 / 上传素材拖入的新图 | 必须标记生成视频时从哪里拷贝到工具 Working。 |
| 一致性参考检查 | `HOST` 和 `Product` 都必须独立检查真实最终文件；缺任一项只 warning，不 blocked，但前端缓存必须失效并展示缺失项。 |

### 16.2 每个工具都需要确认的通用问题

以下问题来自 SessionDesign-R2 的通用工具实现要求，`05_01` 实现前也必须回答。

| 通用问题 | `05_01_VideoPlanGenerator` 第一版建议答案 |
|---|---|
| 是否最小程度生成中间文件和产出物？ | 是。只生成 Working 输入快照、`Output/video_generation_plan.json`、`Report/Result.json`，并同步覆盖 `SessionOutput/storyboard/video_generation_plan.json`。不生成 HTML，不生成重复 snapshot，不生成模型 Prompt。 |
| 是否需要连接数据库？ | 否。只读取本 Session 的 `SessionContext/Variables.json` 和 `SessionOutput/storyboard/srt_storyboard.json`。缺字段时 blocked，不查库补救。 |
| 是否需要产出或更新 SessionContext？ | 否。第一版不写 `SessionContext/Variables.json`，因为计划 JSON 是 StoryBoard 后续工具消费的业务产物，不是全局上下文变量。 |
| 本工具产出物是什么，给后面哪一步使用？ | 产出 `video_generation_plan.json`，给后续音频生成、图片提示词生成、图片生成、视频提示词生成、视频生成、Scene 拼接工具消费。缺失时后续生成类工具必须 blocked。 |
| 如果产出物缺失，下游应该 blocked、fallback 还是 warning？ | 下游视频生成链路必须 blocked；界面报告可以 warning 并提示先运行 `05_01`。 |
| 是否按照 Rerun 和断点继续实现？ | 是。`--force` 清理并重建本工具目录和本工具拥有的 `SessionOutput/storyboard/video_generation_plan.json`；如果本次 force 来自主界面素材删除、媒体绑定变化或前端明确重新生成计划，还必须清理或隔离旧 `05_02` execution state/result，避免旧完成状态污染新 plan；`--resume` 在 StoryBoard 输入 hash、参数 hash 一致且 Output 可信时复用。 |
| 原始状态是什么？ | `S8_05_01_VideoPlanGenerator/` 不存在或为空，且 `SessionOutput/storyboard/video_generation_plan.json` 不存在。 |
| 强制 Rerun 如何恢复原始状态？ | 清理 `S8_05_01_VideoPlanGenerator/Working`、`Output`、`Report`，以及本工具拥有的 `SessionOutput/storyboard/video_generation_plan.json`。如果 StoryBoard 媒体绑定已经变化，还要同步清理或隔离 `video_plan_execution_state.json`、`video_plan_execution_result.json` 和 `S9_05_02_VideoPlanExecutor/`；不删除上游 StoryBoard、不删除素材本体、不改 `SessionContext`。 |
| 断点续跑如何识别已完成且可信的子步骤？ | 使用 `Working/State_progress.json` 记录 StoryBoard 输入 hash、参数 hash、目标范围、输出路径和完成状态；hash 一致且 JSON schema 校验通过时可复用。 |
| 是否需要 Prompt 目录？ | 不需要。第一版不调用模型，不创建 `Prompt/` 目录。 |

## 17. 已确认第一版范围

第一版范围：

1. 只输出独立 `video_generation_plan.json`。
2. 每次运行覆盖本工具拥有的 `SessionOutput/storyboard/video_generation_plan.json`。
3. 不写回 `srt_storyboard.json`。
4. 不调用任何模型。
5. 不在本工具中生成 HTML 报告。
6. 不创建 `Prompt/` 目录。
7. 支持 Scene / Shot / Task 三种范围。
8. 默认 `max-video-seconds = 4.0`。
9. 默认 `min-video-seconds = 4.0`，短句视频按视频模型最小时长生成。
10. 多图片 Scene 中，单个图片区段超时也继续用尾帧切分。
11. 每句都有图片时，每句仍单独生成视频；即使 dialogue 很短，视频生成时长也可以超过原 dialogue 时长。
12. 单条 Dialogue 超过 4 秒时不切割，单独成段。
13. 只有落位到新图片槽位的图片才能做视频首帧。
14. 旧图片只能作为生成新图片的参考，不能直接作为视频首帧。
15. 从原素材 / 上传素材拖入的新图片必须记录素材拷贝来源和目标 Working 路径。
16. 视频文件使用该视频片段第一个 Dialogue 的 key 命名和落位。
17. 尾帧依赖必须显式写入 plan。
18. 第一个 Scene 没有视觉来源时 skipped，不生成。
19. Scene 局部 blocked 时，整体计划仍输出，状态为 `completed_with_blocked_items`，后续可执行 Scene 继续规划。
20. 计划顶层输出 `consistency_references`，检查 `SessionContext/Consistency/HOST.*` 和 `SessionContext/Consistency/Product.*`，分别代表人物一致性和产品一致性最终结果图片；缺失时只记录，不阻断。
21. 绑定视频 Dialogue 作为新的 Segment 起点，且视频步骤完成；执行时由 `05_02` 拷贝到规范 Working 视频路径。
22. 空镜 Segment 的尾帧不可继承；后续空 Dialogue / 空 Scene 只有在前一 Segment 是口播时才能继续。

## 18. 本轮审查：死胡同与逻辑锁死

### 18.0 用户最终确认记录

本节记录 2026-05-31 用户对“绑定视频、空镜尾帧、Scene 边界、05-01 / 05-02 职责”的最终确认，后续实现以此为准：

1. 绑定视频不用管视频时长，后期剪辑合并时处理。
2. 绑定视频只代表视频步骤完成，不代表音频步骤完成；Dialogue 音频仍按 `dialogue_audio_tasks[]` 判断。
3. 绑定视频路径必须真实存在且非空；不存在时不能作为 `bound_video` 完成态来源。
4. 绑定视频必须由 `05_02` 拷贝到规范 Working 路径，不能直接把上传源路径当最终视频。
5. 绑定视频尾帧必须从规范 Working 视频提取。
6. 空镜只阻断后续“无视觉来源续接”；后续 Dialogue 如果自己有原图 / 新图 / 绑定视频，仍可正常开启新 Segment。
7. 旧数据没有 `dialogue.video_plan.is_talking_head` 时，默认按口播处理，不默认 blocked。
8. Segment 不跨 Scene，但尾帧依赖可以跨 Scene；跨 Scene 续接也必须遵守空镜尾帧不可继承。

最终实现原则：`05_01` 只做规划和阻断判断；`05_02` 只按计划执行拷贝 / 生成 / 提取尾帧，不重新推断 Segment 边界，也不绕过空镜阻断。

### 18.1 绑定视频时长不参与 05-01 / 05-02 判断

绑定视频 Segment 的时间范围仍由被覆盖的 Dialogue 时间决定。绑定视频本身可能长于或短于 Dialogue 时间，但这不影响 05-01 / 05-02：

1. `05_01` 不裁剪、不拉伸，只记录 `duration`、`planned_video_duration` 和绑定视频路径。
2. `05_02` 不因为绑定视频时长不匹配而 failed，也不回退到重新生成。
3. 后期剪辑 / 合并阶段负责按时间线处理裁剪、对齐、留白或其它时长适配。
4. 只要绑定视频文件存在且可拷贝，Video 步骤即可完成；不要求 05-02 探测视频时长。

### 18.2 绑定视频尾帧缺失

绑定视频 Segment 可以完成视频步骤，但后续空 Dialogue 如果需要继承尾帧，必须有可用尾帧：

1. `05_01` 规划 `tail_frame.planned_path`。
2. `05_02` 从规范 Working 视频路径提取尾帧。
3. 如果提取失败，依赖该尾帧的后续 Segment failed / blocked；不能偷偷使用上传源视频或更早的尾帧替代。

### 18.3 空镜尾帧阻断范围

空镜阻断不是只阻断 Sync，而是阻断“无视觉来源时继续用尾帧生成”：

1. 同 Scene 内，空镜 Segment 后面的空 Dialogue 不能用空镜尾帧继续。
2. 跨 Scene 时，下一个 Scene 空开头也不能用上一 Scene 空镜尾帧继续。
3. 如果后续 Dialogue 自己有原图 / 新图 / 绑定视频，则不依赖空镜尾帧，可以正常开启新 Segment。

### 18.4 口播状态缺失的兼容策略

旧 StoryBoard 可能没有 `dialogue.video_plan.is_talking_head`：

1. 为了不把老数据大面积锁死，字段缺失时按口播尾帧可继承处理。
2. 用户明确设置 `false` 后才进入空镜阻断。
3. 如果后续产品希望更严格，可以新增“未知需人工确认”状态，但第一版不引入第三态，避免计划大量 blocked。

### 18.5 绑定视频与音频任务的边界

绑定视频只表示视觉视频步骤完成，不自动表示 Dialogue 音频已完成：

1. Dialogue 音频仍按 `dialogue_audio_tasks[]` 检查。
2. 如果最终成片需要原视频声音，需另立字段声明；第一版默认仍使用 StoryBoard/TTS 音频时间线。
3. 不能因为绑定视频存在就跳过缺失 Dialogue 音频的提示。

### 18.6 绑定视频覆盖多个 Dialogue 的归属

绑定视频 Segment 使用该 Segment 第一个 Dialogue 的 key 命名和落位：

1. 一个 Dialogue 最多属于一个 Segment。
2. 一个绑定视频 Segment 可以覆盖多个 Dialogue，直到遇到下一个视觉来源。
3. 如果用户后来在 Segment 中间的 Dialogue 添加图片或视频，必须重新运行 `05_01`，重新切段，旧执行结果只能按 plan hash 作为历史查看。

### 18.7 Scene 边界

Segment 不跨 Scene，这能避免一个绑定视频从上一 Scene “吃掉”下一 Scene：

1. 上一 Scene 的最后 Segment 可以提供尾帧依赖，但不能把下一 Scene 的 Dialogue 纳入同一个 Segment。
2. 如果上一 Scene 是绑定视频且标记口播，可通过尾帧让下一 Scene 空开头继续生成。
3. 如果上一 Scene 是绑定视频且标记空镜，下一 Scene 空开头 blocked，除非下一 Scene 自己有原图 / 新图 / 绑定视频。
