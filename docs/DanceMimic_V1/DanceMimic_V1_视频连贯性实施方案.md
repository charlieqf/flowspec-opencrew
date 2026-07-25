# DanceMimic_V1 视频连贯性实施方案

版本：v0.8
状态：Implementation-ready 草案，已按代码事实校正
适用范围：DanceMimic_V1 StoryBoard 生成、VideoPlan 生成与执行链路

## 0. 关键事实校正

本方案基于现有代码重新校正以下事实：

1. `working_assets.images[]` 里只有两个真实图片槽：
   - `Image_New`
   - `Image_02`

   `Image_Source` 不是 `working_assets.images[]` 的成员。

2. UI 里的“原图”不是 `working_assets.images[]` 槽位，而是 Dialogue 级 source image binding：
   - `dialogue.source_image_paths[0]`
   - `dialogue.image_path`
   - 绑定/物化时可落到标准文件名 `{dialogue_asset_key}_Image_Source.*`

   该标准文件名不是本方案杜撰的新约定。现有后端 `working_slot_path(asset_key, slot, suffix)` 会按 `{asset_key}_{slot}{ext}` 生成 Working 路径，`asset_reference_services.py` 已用 `Image_Source` 作为 source binding 的标准 slot 名；但它仍然不是 `working_assets.images[]` 成员。

3. 当前 DanceMimic builder 的实际行为是：
   - 每个 segment 都把目标身份图复制到 `{dak}_Image_New.*`
   - `working_assets.images[0] = Image_New`
   - `source_type = dance_mimic_target_identity`
   - `Image_02` 为空
   - `image_path` 为空
   - 未把目标身份图写入“原图”绑定字段

4. 通用 Analysis_V1 / VideoOnly 管线已经有尾帧续接基础设施：
   - `TailSource`
   - `depends_on_segment_id`
   - `depends_on_tail_frame_path`
   - `first_frame.source_type = previous_segment_tail_frame`
   - `first_frame.source_type = previous_scene_tail_frame`
   - `materialize_first_frame`

   因此本方案不是新增一套尾帧字段，而是要求 DanceMimic 正确复用已有字段，并补齐执行 gate。

5. UI 需要区分两层：
   - 主 DialogueCard 媒体面板渲染 `Audio / 原图 / 新图 / 新视频 / 终视频`。
   - 左侧 Sidebar 绑定指示渲染 `声音 / 原图 / 新图 / 终视频`，不包含“新视频”。

## 1. 背景与问题

Task 175 的 DanceMimic_V1 流程中，参考舞蹈视频 `dance_solo_bigmotion_studio.mp4` 被按时间拆成两个连续 segment：

| Segment | 时间范围 | 帧范围 | 参考视频 |
| --- | --- | --- | --- |
| `dak_0001` | `0.000s - 6.006s` | `0 - 143` | `dak_0001_Reference_FaceMasked.mp4` |
| `dak_0002` | `6.006s - 11.970s` | `144 - 286` | `dak_0002_Reference_FaceMasked.mp4` |

这两个遮脸参考视频来自同一个原始舞蹈视频的相邻时间段，不是重复视频。它们在动作来源上连续，但当前最终生成结果仍是分段独立生成：

- `dak_0001` 的生成结果尾帧没有稳定驱动 `dak_0002` 的首帧。
- 当前 DanceMimic builder 把每段目标身份图都放进 `Image_New`，导致每段都有自己的独立首帧 anchor。
- 通用 TailFrame 字段存在，但 DanceMimic 没有正确写入并强制消费这套依赖。

因此，即使参考视频本身连续，最终拼接的视频仍可能出现人物姿态、衣服纹理、空间位置或脸部状态跳变。

## 2. 目标

本方案目标是让 DanceMimic 的多 segment 视频生成具备结果级连贯性：

1. Segment 1 正常生成。
2. Segment 1 完成后提取最终视频 `TailFrame`。
3. Segment 2 生成前，把 Segment 1 的 `TailFrame` 物化为 Segment 2 的 `Image_New`。
4. Segment N 必须依赖 Segment N-1 的最终尾帧。
5. 同时保留目标身份图作为身份参考，避免单纯使用上一段尾帧导致人物身份弱化。

最终期望：

- 拼接点前后首尾画面尽量连续。
- 每个 segment 仍稳定贴近目标人物身份。
- 普通口播 StoryBoard 的行为不受影响。

## 3. 非目标

本方案不处理以下问题：

- 不改变 DanceMimic 的参考视频切分策略。
- 不新增视频生成模型或供应商。
- 不新增 UI 顶层素材槽位。
- 不新增 `working_assets.images[]` 的第三个图片槽。
- 不把 `Image_02` 暴露成新的 UI 槽位。
- 不重建通用 TailFrame 字段和枚举。
- 不修改普通 StoryBoard / 口播视频的默认行为。

## 4. 设计决策

本方案选择复用现有“原图”绑定来保存目标身份图，复用 `Image_New` 来保存首帧/续接帧。

不采用以下方案：

| 方案 | 是否采用 | 原因 |
| --- | --- | --- |
| 把目标身份图放入 `Image_02` | 不采用 | `Image_02` 当前不是 UI 顶层语义，也不是 05_01/05_02 的视频首帧输入；使用它会扩大 planner、executor、UI 改造面。 |
| 新增 `working_assets.images[].Image_Source` | 不采用 | `working_assets.images[]` 当前固定为 `Image_New` / `Image_02`，新增槽会破坏现有数据模型和兼容性。 |
| 使用 Dialogue source image binding | 采用 | UI 已有“原图”，后端已有 `source_image_paths[0]` / `image_path` 和 `{asset_key}_Image_Source.*` 标准文件路径，可承载目标身份图。 |

采用后的语义：

| 概念 | 真实字段 / 文件 | DanceMimic 语义 |
| --- | --- | --- |
| 目标身份图 | `source_image_paths[0]` / `image_path`，标准文件 `{dak}_Image_Source.*` | 身份参考，不作为 Segment 2+ 的首帧 anchor |
| 首帧 / 续接帧 | `working_assets.images[0]`，slot `Image_New`，标准文件 `{dak}_Image_New.*` | 视频生成首帧；Segment 2+ 来自上一段 `TailFrame` |
| 备用图片槽 | `working_assets.images[1]`，slot `Image_02` | 本方案不使用 |
| 生成视频 | raw video artifact，通常 `{dak}_Video_Raw.mp4` | 主 DialogueCard 的“新视频”，不是 Sidebar 绑定指示 |
| 最终视频 | `working_assets.video`，slot `Video_Final` | 主 DialogueCard 和 Sidebar 的“终视频” |

重要约束：

- DanceMimic 的 `source_image_paths[0]` 是身份参考，不应让 `visual_for_dialogue()` 在 Segment 2+ 把它当作首帧 anchor。
- Segment 2+ 的首帧来源必须显式写为 `previous_segment_tail_frame`。
- `Image_New` 是视频模型第一张输入图，承担连续性职责。

## 5. 生成输入关系

推荐输入关系如下：

| Segment | `Image_New` 首帧输入 | Source image 身份输入 | 动作参考输入 |
| --- | --- | --- | --- |
| Segment 1 | 目标身份图副本 | 目标身份图 | Segment 1 遮脸参考视频 |
| Segment 2 | Segment 1 最终视频尾帧 | 目标身份图 | Segment 2 遮脸参考视频 |
| Segment N | Segment N-1 最终视频尾帧 | 目标身份图 | Segment N 遮脸参考视频 |

其中：

- `Image_New` 负责视频连续性。
- source image binding 负责目标人物身份稳定性。
- 遮脸参考视频负责动作、姿态节奏和镜头运动。

Segment 1 中 `Image_New` 与 source image 可以来自同一张目标身份图；执行时应去重或在 request metadata 中标明双重角色。

评审/验收目标身份图使用：

```text
/Users/macmini-4/work/code/OpenCrew/docs/DanceMimic_V1/target_figure.jpg
```

该文件为机器人目标形象图，已核实为 JPEG，3213x5712。实现时应把它作为 target identity image 输入：每个 DanceMimic segment 的 source image binding 指向该图的标准 Working 副本；Segment 1 的 `Image_New` 也可使用它的副本作为第一段首帧。

## 6. StoryBoard Builder 改动

目标文件：`ToolLibrary/DanceMimic_V1/_tool_impl.py`

当前 builder 把目标身份图写入所有 segment 的 `Image_New`。这会导致后续 segment 以身份图重新起步，而不是从上一段生成结果续接。

需要改为：

### 6.1 每个 segment 都写入身份参考

为每个 `dak` 复制目标身份图到 source image 标准文件：

```text
SessionOutput/storyboard/Working/{dak}_Image_Source.{ext}
```

并写入：

```json
{
  "image_path": "SessionOutput/storyboard/Working/{dak}_Image_Source.{ext}",
  "source_image_paths": [
    "SessionOutput/storyboard/Working/{dak}_Image_Source.{ext}"
  ],
  "dance_mimic": {
    "target_identity_image_path": "SessionOutput/storyboard/Working/{dak}_Image_Source.{ext}"
  }
}
```

这里的 `Image_Source` 只是标准文件命名和 source image binding，不是 `working_assets.images[]` slot。

### 6.2 Segment 1 写入可用首帧

Segment 1 仍需要一个 `Image_New`：

```json
{
  "working_assets": {
    "images": [
      {
        "slot": "Image_New",
        "source_type": "dance_mimic_target_identity",
        "path": "SessionOutput/storyboard/Working/dak_0001_Image_New.{ext}"
      },
      {
        "slot": "Image_02",
        "source_type": "",
        "path": ""
      }
    ]
  }
}
```

`dak_0001_Image_New.*` 可以是目标身份图副本，用于第一段起始画面。

### 6.3 Segment 2+ 不预填目标身份图到 `Image_New`

Segment 2 及之后：

- `source_image_paths[0]` 保持目标身份图。
- `working_assets.images[0]` 不再预填目标身份图。
- `Image_New` 应等待 05_01/05_02 依据上一段 `TailFrame` 物化。

推荐初始结构：

```json
{
  "working_assets": {
    "images": [
      {
        "slot": "Image_New",
        "source_type": "",
        "path": ""
      },
      {
        "slot": "Image_02",
        "source_type": "",
        "path": ""
      }
    ]
  }
}
```

## 7. VideoPlan Generator 改动

目标文件：`ToolLibrary/Analysis_V1/05_01_VideoPlanGenerator.py`

现有通用能力已经包括：

- `TailSource`
- `planned_tail_for_segment()`
- `visual_from_tail()`
- `first_frame.materialize_first_frame`
- `dependencies.depends_on_segment_id`
- `dependencies.depends_on_tail_frame_path`

DanceMimic 需要做的是正确使用这些能力，而不是新增另一套字段。

下游路径已核实：DanceMimic 00-03 是独立 runner，只负责生成 DanceMimic StoryBoard 与 seed；进入 StoryBoard 页面后的 VideoPlan 生成和执行分别由通用 `05_01_VideoPlanGenerator.py` 与 `05_02_VideoPlanExecutor.py` 承担。也就是说，本节改动落点是 DanceMimic StoryBoard 进入通用 05_01/05_02 之后的计划与执行链路，不是把 DanceMimic 00-03 runner 改成 Analysis_V1 七步链。

### 7.1 DanceMimic Segment 1

Segment 1 的 plan：

```json
{
  "first_frame": {
    "source_type": "generated_image",
    "source_path": "SessionOutput/storyboard/Working/dak_0001_Image_New.png"
  },
  "dependencies": {
    "depends_on_segment_id": "",
    "depends_on_tail_frame_path": ""
  }
}
```

这里不能使用 `existing_image`，因为它不是当前合法枚举。按本方案，Segment 1 的目标身份图副本已经落在 `Image_New`，通用 `new_image_visual()` 会把 Working 下的 `Image_New` 识别为 `generated_image`。如果后续实现改成只从 source image 生成首帧，则才会走 `original_image`，但那不是本方案推荐路径。

### 7.2 DanceMimic Segment 2+

Segment 2 及之后必须显式使用上一段 tail：

```json
{
  "first_frame": {
    "source_type": "previous_segment_tail_frame",
    "source_path": "SessionOutput/storyboard/Working/dak_0001_TailFrame.png",
    "materialize_first_frame": {
      "required": true,
      "copy_from_path": "SessionOutput/storyboard/Working/dak_0001_TailFrame.png",
      "copy_to_path": "SessionOutput/storyboard/Working/dak_0002_Image_New.png",
      "source_type": "previous_segment_tail_frame"
    }
  },
  "dependencies": {
    "depends_on_segment_id": "dak_0001",
    "depends_on_video_path": "SessionOutput/storyboard/Working/dak_0001_Video_Final.mp4",
    "depends_on_tail_frame_path": "SessionOutput/storyboard/Working/dak_0001_TailFrame.png"
  }
}
```

注意：因为 DanceMimic 的 source image binding 会存在于每个 segment，如果通用 `visual_for_dialogue()` 直接把“原图”当作 anchor，会绕过尾帧续接。05_01 需要在 DanceMimic 模式下增加专门规则：

- Segment 1 可使用 `Image_New` 或 source image 初始化首帧。
- Segment 2+ 必须优先使用 `planned_tail_for_segment(previous_segment)`。
- Segment 2+ 的 source image 只进入身份参考元数据，不参与 first frame 选择。

实现时必须把这条 DanceMimic tail 规则放在 `visual_for_dialogue()` 的 source image fallback 之前短路命中。原因是 `new_image_visual()` 对 Working 目录下的 `Image_New` 会无条件判为 `generated_image`，而 Segment 2+ 按本方案初始会把 `Image_New` 留空；如果此时继续走通用 `visual_for_dialogue()`，它会回退到 `old_image_visual()`，把 source image binding 中的目标身份图判为 `original_image` 首帧 anchor，导致 Segment 2+ 再次从身份图起步，绕过上一段 `TailFrame`。

### 7.3 启用判定

不要使用不存在的 `storyboard.dance_mimic.enabled`。

建议使用真实条件：

- task/workspace 级 `workflow_mode == "dance_mimic_v1"`
- 或 05_01 现有 `dialogue_has_dance_mimic_reference(dialogue)` 判定
- 或 segment/dialogue 存在 `dance_mimic.reference_video_path`

## 8. VideoPlan Executor 改动

目标文件：`ToolLibrary/Analysis_V1/05_02_VideoPlanExecutor.py`

05_02 目前会按 plan 顺序遍历 segment，并已有 `materialize_first_frame` 复制逻辑。但 DanceMimic 还需要更严格的执行 gate：

1. Segment N，N > 1，开始前读取：
   - `dependencies.depends_on_segment_id`
   - `dependencies.depends_on_video_path`
   - `dependencies.depends_on_tail_frame_path`
   - `first_frame.materialize_first_frame.copy_from_path`
   - `first_frame.materialize_first_frame.copy_to_path`

2. 执行前必须校验：
   - 上一段执行结果成功。
   - 上一段最终视频存在。
   - 上一段 `TailFrame` 存在且可读。
   - 当前段目标身份图存在。
   - 当前段遮脸参考视频存在。

3. 校验通过后，物化：
   - 复制上一段 `TailFrame` 到当前段 `{dak}_Image_New.*`
   - 更新当前 Dialogue 的 `working_assets.images[0]`
   - 执行器现有物化逻辑会把绑定后的 `source_type` 记录为 `tail_frame_materialized`
   - 同步 `srt_storyboard.json` 与 `koubo_storyboard_edit.json`

4. 校验失败时：
   - 当前 segment 标记为 blocked/failed。
   - DanceMimic 后续 segment 不应继续生成。
   - 不允许静默回退到目标身份图继续生成。

通用 05_02 当前异常处理会记录失败并继续遍历后续 segment。DanceMimic 模式需要额外 hard gate，避免后续段在上游失败后继续独立生成。

## 9. 模型输入与 Prompt 约束

DanceMimic 的视频模型调用应使用三类输入：

| 输入 | 来源 | role |
| --- | --- | --- |
| 第一张图片 | 当前 segment 的 `Image_New` | `continuity_first_frame` |
| 第二张图片 | 当前 segment 的 source image binding / `dance_mimic.target_identity_image_path` | `target_identity` |
| 视频 | 当前 segment 的 `dance_mimic.reference_video_path` | `motion_reference_video` |

Segment 1 如果两张图片内容相同，可以去重，但 request metadata 应记录：

```json
{
  "role": ["continuity_first_frame", "target_identity"]
}
```

Prompt 需要明确三者角色：

```text
Use the first image as the exact starting frame and continue motion naturally from it.
Use the identity reference image to preserve the target person's appearance consistently.
Use the masked reference video only for dance motion, pose timing, and camera movement.
Do not copy the masked reference video's person identity.
```

## 10. UI 展示方案

不新增顶层槽位。

### 10.1 DialogueCard

主卡片现有媒体项为：

| 当前项 | DanceMimic 建议语义 |
| --- | --- |
| Audio | 可为空；DanceMimic 默认不依赖口播音频 |
| 原图 | 目标身份 |
| 新图 | 首帧 / 续接帧 |
| 新视频 | Raw 生成视频 |
| 终视频 | Final 视频 |

Segment 2+ 的“新图”可展示来源状态：

- 等待上一段尾帧
- 已使用上一段尾帧
- 尾帧缺失
- 人工替换

### 10.2 Sidebar

左侧绑定指示仍为：

| 当前项 | DanceMimic 建议语义 |
| --- | --- |
| 声音 | 声音绑定状态 |
| 原图 | 目标身份是否存在 |
| 新图 | 首帧 / 续接帧是否存在 |
| 终视频 | 最终视频是否存在 |

Sidebar 不显示“新视频”，因为 raw video 是执行流水线产物，不是当前 Sidebar 绑定指示项。

### 10.3 Audio 槽与参考音频来源

DanceMimic 不应从 TTS 生成音频。跳舞模仿的默认音频来源必须是原始未遮脸参考舞蹈视频的音轨，不是遮脸后的 segment reference video，也不是视频模型生成结果里的音轨。

1. 用户创建 DanceMimic task 时选择的参考视频会进入 workspace，并作为原始参考视频：
   - `SessionContext/Video_Reference_Source.mp4`
2. `01_ReferenceMediaDemux` 从该原始参考视频提取混合音频：
   - `SessionOutput/reference/Audio_Reference_Mixed.wav`
3. `03_StoryBoardStandardTaskBuild` 会把该音频复制到 StoryBoard assets：
   - `SessionOutput/storyboard/assets/audios/Audio_Reference_Mixed.wav`
4. `storyboard_seed.json.mixed_audio_path` 记录这个全局参考音频路径。

评审/验收参考视频使用：

```text
/Users/macmini-4/work/code/OpenCrew/docs/DanceMimic_V1/reference_video.mp4
```

该文件已核实包含音轨：AAC stereo，44.1kHz，约 34s，可用于验证“从原始参考视频获取声音并按 segment 切片绑定”的链路。

当前实现缺口：

- `03` 只把 mixed audio 作为全局 asset/seed 字段保存，没有切成每个 segment 的 `Audio_Final`。
- DanceMimic 在 `05_01` 中会设置 `need_audio = false`，并清空 dialogue audio tasks。
- `05_02` 发现 DanceMimic 没有 dialogue audio files 时，会生成静音 `SegmentAudio_Final.wav`。
- 所以 UI 里的每段 Audio 槽为空，且最终视频可能只有静音音轨。

推荐实现：

- 如果 `mixed_audio_path` 指向真实有声文件，则按每个 segment 的 `start/end` 切出段音频。
- 切出的音频绑定到当前 dialogue 的 `working_assets.audio`，slot 为 `Audio_Final`，路径为 `SessionOutput/storyboard/Working/{dak}_Audio_Final.wav`。
- 切片边界来自参考视频时间轴，但合成前必须以生成视频的实际时长为准做 pad/trim 对齐。不能直接假设参考段 `end-start` 等于模型输出时长；模型可能因 `target_video_seconds` 取整、帧率或供应商行为产生轻微差异。否则会出现有声音但音画不同步、结尾截断或音频拖尾。
- `05_02` 合成最终视频时优先使用这个段音频，而不是生成静音。
- 如果源参考视频本身没有 audio stream，则保留空 Audio 槽或显示“参考视频无音轨”，并允许静音结果。

Task 175 的实际情况：`SessionContext/Video_Reference_Source.mp4` 没有 audio stream，`Audio_Reference_Mixed.wav` 是 `generated_silence`，因此“跑完没有可听音频”符合当前素材事实。但即使源视频有音轨，现有实现也还缺少“把 mixed audio 切片并绑定到每段 Audio 槽”的步骤。

## 11. 兼容性边界

该机制只在 DanceMimic 工作流启用。

普通 StoryBoard、口播视频、非 DanceMimic 图生视频任务保持现有行为：

- 不改变 source image binding 的原有语义。
- 不把 source image 强制解释为身份参考。
- 不强制所有 segment 串行等待上一段 tail。
- 不额外传入第二张身份参考图。
- 不改变 Sidebar 或 DialogueCard 的默认通用文案。

## 12. 数据记录与可审计性

建议在 VideoPlan、执行日志和 model call request 中记录：

```json
{
  "workflow_mode": "dance_mimic_v1",
  "continuity_mode": "previous_segment_tail_frame",
  "identity_reference_image_path": "SessionOutput/storyboard/Working/dak_0002_Image_Source.{ext}",
  "first_frame_path": "SessionOutput/storyboard/Working/dak_0002_Image_New.png",
  "first_frame_source": {
    "type": "previous_segment_tail_frame",
    "segment_id": "dak_0001",
    "tail_frame_path": "SessionOutput/storyboard/Working/dak_0001_TailFrame.png"
  },
  "reference_inputs": [
    {
      "path": "SessionOutput/storyboard/Working/dak_0001_TailFrame.png",
      "role": "continuity_first_frame"
    },
    {
      "path": "SessionOutput/storyboard/Working/dak_0002_Image_Source.{ext}",
      "role": "target_identity"
    },
    {
      "path": "SessionOutput/storyboard/assets/videos/dak_0002_Reference_FaceMasked.mp4",
      "role": "motion_reference_video"
    }
  ]
}
```

## 13. 失败处理策略

| 场景 | 处理 |
| --- | --- |
| 上一段生成失败 | 阻断当前及后续 DanceMimic segment |
| 上一段最终视频缺失 | 阻断当前及后续 DanceMimic segment |
| TailFrame 提取失败 | 阻断当前及后续 DanceMimic segment |
| 目标身份图缺失 | 阻断当前 segment |
| 当前段遮脸参考视频缺失 | 阻断当前 segment |
| TailFrame 质量较差 | 默认阻断或标记需人工确认 |
| 用户明确选择忽略连续性 | 允许回退到目标身份图，但必须记录 warning 和 manual override |

不建议默认自动回退到目标身份图，因为这会掩盖连贯性问题。

## 14. 测试方案

### 14.1 Builder 合同测试

需要覆盖：

- `working_assets.images[]` 仍只有 `Image_New` / `Image_02`。
- 每个 DanceMimic dialogue 都有 `source_image_paths[0]` / `image_path` 指向 `{dak}_Image_Source.*`。
- 每个 dialogue 的 `dance_mimic.target_identity_image_path` 指向身份参考图。
- Segment 1 有 `Image_New`。
- Segment 2+ 不再预填目标身份图到 `Image_New`。
- 普通 StoryBoard 不受影响。

### 14.2 VideoPlan 合同测试

需要覆盖：

- Segment 1 的 first frame 使用实际可用首帧。
- Segment 2+ 的 `first_frame.source_type == "previous_segment_tail_frame"`。
- Segment 2+ 的 `dependencies.depends_on_segment_id` 指向上一段。
- Segment 2+ 的 `dependencies.depends_on_tail_frame_path` 指向上一段 `{dak}_TailFrame.png`。
- Segment 2+ 即使存在 source image binding，也不会把 source image 作为 first frame anchor。

### 14.3 Executor 测试

需要覆盖：

- 执行器按顺序执行 DanceMimic segments。
- Segment 1 完成后提取 `TailFrame`。
- Segment 2 开始前，`Image_New` 被上一段 `TailFrame` 物化。
- 模型调用包含：
  - 当前 `Image_New`
  - 目标身份 source image
  - 当前遮脸参考视频
- 当上一段失败或 TailFrame 缺失时，后续 DanceMimic segment 被阻断，不继续独立生成。
- 当 `mixed_audio_path` 指向真实有声参考音频时，执行器使用切片后的段音频，而不是生成静音 `SegmentAudio_Final.wav`。
- 段音频切片后按生成视频实际时长 pad/trim，对齐后的音频时长应与最终视频时长一致或在允许误差内。

### 14.4 UI 验收

需要覆盖：

- DialogueCard 不新增图片槽位。
- DialogueCard 的“原图”显示目标身份。
- DialogueCard 的“新图”显示首帧/续接帧。
- 源参考视频有音轨时，DialogueCard 的 Audio 槽显示当前 segment 的切片音频。
- 源参考视频无音轨时，Audio 槽允许为空或显示“参考视频无音轨”，但不能误导用户以为 TTS 会自动生成。
- Sidebar 仍只有 `声音 / 原图 / 新图 / 终视频` 绑定指示。
- 普通口播 StoryBoard 的显示不变。

### 14.5 Task 175 回归

使用 Task 175 或等价 fixture 验证：

- `dak_0001` 与 `dak_0002` 的遮脸参考视频仍来自同一原始视频的相邻切片。
- `dak_0002_Image_New.png` 在执行前由 `dak_0001_TailFrame.png` 物化。
- `dak_0002` 的 model call request 中同时包含：
  - `dak_0002_Image_New.png`
  - `source_image_paths[0]` 指向的目标身份图路径，通常为 `dak_0002_Image_Source.*`
  - `dak_0002_Reference_FaceMasked.mp4`
- 最终拼接视频在 `6.006s` 附近没有明显首尾跳变。

## 15. 实施步骤

建议分五步落地：

1. 修正 DanceMimic builder
   - 目标身份图写入 source image binding。
   - Segment 1 保留 `Image_New`。
   - Segment 2+ 不再预填身份图到 `Image_New`。

2. 修正 05_01 DanceMimic plan
   - 复用通用 `TailSource` / `visual_from_tail()`。
   - Segment 2+ 强制写入 `previous_segment_tail_frame`。
   - source image binding 只作为身份参考，不作为后续段首帧 anchor。

3. 修正 05_02 DanceMimic executor
   - 增加上游 tail gate。
   - 物化上一段 `TailFrame` 到当前 `Image_New`。
   - 模型调用增加目标身份 source image。
   - 上游失败时阻断后续段。
   - 生成视频完成后提取 `TailFrame`，并把状态写回执行结果。
   - 如果存在真实 `mixed_audio_path`，使用切片并按生成视频实际时长 pad/trim 后的段音频；否则才允许静音。

4. 修正 DanceMimic 音频链路
   - 从原始未遮脸参考视频提取 `Audio_Reference_Mixed.wav`。
   - 按 segment `start/end` 初切参考音频。
   - 生成视频完成后，以实际视频时长为准对音频做 pad/trim。
   - 将对齐后的音频绑定为当前 dialogue 的 `Audio_Final`。
   - 源参考视频无音轨时记录 warning，并让 UI 显示“参考视频无音轨”或保持空音频槽。

5. 修正 UI 文案与状态
   - DanceMimic 模式下“原图”解释为目标身份。
   - “新图”解释为首帧/续接帧。
   - Sidebar 不增加“新视频”指示。
   - Audio 槽在有参考音频时显示当前段切片音频；无参考音轨时不提示用户生成 TTS。

## 16. 实现决策

以下决策作为本轮实现默认行为，不再作为阻塞评审项：

1. Segment 2+ 的 `Image_New` 初始保持空路径。
   - UI/plan/status 可以展示“待物化”状态。
   - 不允许先填目标身份图再由执行器隐式覆盖，因为这会让 UI 展示和执行语义不一致。

2. TailFrame 缺失或上游失败时默认阻断。
   - 不自动回退到目标身份图。
   - 只有用户显式选择忽略连续性时，才允许 manual override，并必须记录 warning。

3. TailFrame 质量检测不是本轮 P0 阻塞项。
   - 本轮至少校验文件存在、可读、非零大小。
   - 黑帧、清晰度、人脸/主体可见性检测作为 P1/P2。

4. 人工替换续接帧不是本轮 P0 阻塞项。
   - 后续支持时必须标记来源为 `manual_override`。

5. VideoOnly 入口不应绕过 DanceMimic 连续性 gate。
   - 如果 VideoOnly 会执行 DanceMimic 多 segment 生成，必须复用同一套 tail gate、身份图输入和音频对齐逻辑。
   - 如果暂时无法复用，应在 DanceMimic 模式禁用或阻断该入口，而不是让它独立生成后续段。

## 17. 结论

DanceMimic 的参考视频切片本身是连续的，但当前生成结果不是连续依赖的。正确修复点不是新增槽位，也不是重建 TailFrame 机制，而是：

- 把目标身份图从“每段 `Image_New`”迁移到 Dialogue source image binding。
- 保留 `Image_New` 专门作为首帧/续接帧。
- 复用已有 `previous_segment_tail_frame` / `depends_on_tail_frame_path` / `materialize_first_frame` 机制。
- 在 DanceMimic 执行时增加上游 TailFrame gate。
- 模型调用同时传入连续首帧、目标身份图和遮脸动作参考视频。

这样可以在不新增 UI 顶层槽位、不改变普通 StoryBoard 的前提下，为 DanceMimic 建立正确的视频连贯性机制。
