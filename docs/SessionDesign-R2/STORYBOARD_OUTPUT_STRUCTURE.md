# Analysis_V1 StoryBoard Output Structure

## 工作目标

`SessionOutput/storyboard/` 是 StoryBoard 的可调整制作工作区。它的核心目标不是保存历史版本，而是保存当前 StoryBoard 结构和当前 Dialogue / SRT 最终素材。

StoryBoard 会被用户反复调整 Shot / Scene 分组、对白、素材选择和参数。因此目录结构必须简单、可覆盖、可由 `srt_storyboard.json` 唯一索引。

口播 StoryBoard 的素材管理粒度是 Dialogue，而不是 Shot 或 Scene。Shot / Scene 只是编排层级，Dialogue / `srt_id` 才是素材身份。只要 Dialogue 仍存在，重新组合 Shot / Scene、重排 Scene、调整 Scene 归属或刷新编号，都不应删除、备份或重新命名该 Dialogue 已绑定的素材。

素材进入 `assets/history/` 的触发条件是：任何新生成并已经物化到 `Working/` 的图片、音频或视频，要从当前 StoryBoard / 当前槽位中物理移除。history 是 `Working/` 的回收站。只有进入 history 后，用户才能在 history 里手动执行最终删除。

常见进入 history 的情况：

- 用户点击删除某个 Dialogue 的新生成图片、音频或视频槽位。
- 用户替换某个 Dialogue 的新生成图片、音频或视频，旧的 `Working/` 文件不再作为当前槽位引用。
- Dialogue 被删除。
- 合并 Dialogue 时，后一个 Dialogue 被并入前一个 Dialogue 并消失。

不进入 history 的情况：

- 原图参考 / 原视频参考帧只存在于 `SessionOutput/visual/srt_frames/`，没有进入 `Working/`。
- 上传素材池本体留在 `assets/images/`、`assets/videos/`、`assets/audios/`，拖入槽位时只做绑定标记，不进入 `Working/`。
- 删除上传素材绑定时，只清空当前槽位绑定，让素材回到上传素材池可用状态，不进入 history。
- 只重排 Shot / Scene，Dialogue 和槽位引用仍存在。

合并 Dialogue 时，保留下来的前一个 Dialogue 继续使用自己的素材；被合并后消失的 Dialogue，只有其中已经进入 `Working/` 的新生成素材才进入 history。原视频素材和上传素材绑定只解除引用，不复制进 history。

## 总体结构

```text
SessionOutput/storyboard/
  srt_storyboard.json

  assets/
    images/
    videos/
    audios/
    history/
      batch_20260530_153012_remove_slot_asset/
        manifest.json
        srt_0001_01_Image_01.png
        srt_0001_01_Audio_Final.wav
        srt_0001_01_Video_Final.mp4

  Working/
    srt_0001_01_Image_01.png
    srt_0001_01_Image_02.png
    srt_0001_01_Audio_Final.wav
    srt_0001_01_Video_Final.mp4

    srt_0001_02_Image_01.png
    srt_0001_02_Audio_Final.wav
    srt_0001_02_Video_Final.mp4
```

参考帧不放在 `SessionOutput/storyboard/` 下。参考帧由 02-02 生成，保存在：

```text
SessionOutput/visual/srt_frames/
```

## 目录职责

### `srt_storyboard.json`

StoryBoard 的唯一结构索引文件。

它负责记录：

- Shot / Scene 层级。
- 每个 Scene 包含哪些 `srt_id`。
- 每个 Scene 的起止时间、时长、对白条目。
- 每个 Scene 的首句参考图。
- 每个 Dialogue 当前生成图片、生成音频、生成视频在 `Working/` 下的文件路径。
- 每个 Dialogue 上传素材绑定路径和来源标记。
- 每个 Dialogue 原图参考路径，例如 `image_path` 和 `key_frame_paths[]`。

任何 UI 或后续工具都必须以 `srt_storyboard.json` 为准，不应该通过扫描目录自行推断 Shot / Scene 结构。

### `assets/images/`

备用上传图片素材池。

用户上传但尚未确定为某个 Dialogue 最终素材的图片放在这里。

### `assets/videos/`

备用上传视频素材池。

用户上传但尚未确定为某个 Dialogue 最终素材的视频放在这里。

### `assets/audios/`

备用上传声音素材池。

用户上传但尚未确定为某个 Dialogue 最终声音的音频放在这里。

### `assets/history/`

历史素材池。

当 `Working/` 中的新生成图片、音频或视频从当前 StoryBoard / 当前槽位物理移除时，必须先归档到这里。历史素材不是当前最终素材，只有当用户再次拖入某个 Dialogue 槽位并保存后，才重新成为当前 StoryBoard 的有效引用。

history 只归档已经物化到 `Working/` 的新生成图片、音频和视频。原视频参考帧 / 原图不进入 `Working`，上传素材绑定也不进入 `Working`，二者都不作为文件副本进入 history；如果需要追踪，可以在 manifest 中记录原 `image_path` 或上传素材路径引用。

history 的删除语义：

- 从当前 Dialogue 槽位删除生成素材：清空当前槽位引用，并把对应 `Working/` 文件移入 history。
- 从当前 Dialogue 槽位删除上传素材绑定：只清空绑定，让上传素材回到上传素材池可用状态。
- 从当前 Dialogue 槽位删除原视频素材绑定：只清空绑定，原视频素材仍留在原视频素材池。
- 从 history 恢复素材：必须复制回 `Working/{target_dialogue_asset_key}_{slot}.{ext}`，并把当前槽位写为 `source_type = "generated"`；当前槽位不得直接引用 history 文件。
- 从 `Working/` 物理清理：必须先移入 history，不能直接永久删除。
- 从 history 手动删除：才是最终删除，可以移除 history 文件和 manifest 条目。

不应进入 history 的情况：

- 只重排 Shot / Scene。
- 只把 Dialogue 从一个 Scene 移到另一个 Scene。
- 只拆分或合并 Shot，但 Dialogue 本身仍存在。
- 只刷新 `shot_id` / `scene_id` 编号。
- 只重新生成并覆盖同一个 `Working/` 文件，且该文件此前没有有效 generated 素材引用。只要旧 generated 文件会被新生成结果替换，旧文件必须先进入 history。
- 只删除上传素材或原视频素材绑定，因为它们没有进入 `Working/`。

推荐结构：

```text
SessionOutput/storyboard/assets/history/
  batch_20260530_153012_remove_slot_asset/
    manifest.json
    srt_0001_02_Image_01.png
    srt_0001_02_Video_Final.mp4
    srt_0001_02_Audio_Final.wav
```

批次目录内部不再按 `images/`、`videos/`、`audios/` 创建子文件夹。`manifest.json` 和该批次回收的所有素材文件平铺在同一级目录下。素材类型由 manifest 中的 `asset_type` 和文件扩展名判断。

`manifest.json` 至少应记录：

```json
{
  "batch_id": "batch_20260530_153012_remove_slot_asset",
  "reason": "merge_dialogue_removed",
  "created_at": 0,
  "items": [
    {
      "asset_type": "Image",
      "history_path": "SessionOutput/storyboard/assets/history/batch_20260530_153012_remove_slot_asset/srt_0001_02_Image_01.png",
      "original_path": "SessionOutput/storyboard/Working/srt_0001_02_Image_01.png",
      "slot": "Image_01",
      "source_dialogue_id": "dialogue_srt_0001_02",
      "source_srt_id": "srt_0001_02",
      "source_scene_id": "scene_001",
      "reason": "merge_dialogue_removed"
    }
  ]
}
```

UI 必须通过 Session Workspace 中的 `assets/images/`、`assets/videos/`、`assets/audios/` 和 `assets/history/` 自行组织右侧素材池，不能只依赖前端内存状态。

### `Working/`

当前 StoryBoard 的 Dialogue 新生成素材最终区。

规则：

- 不允许再按 Shot 或 Scene 创建子目录。
- 新生成图片、音频和视频最终素材平铺在 `Working/` 下。
- 原视频参考帧 / 原图不复制到 `Working/`，只在 Dialogue 字段里保留原始 `SessionOutput/visual/srt_frames/` 路径。
- 上传素材不复制到 `Working/`，只在 Dialogue 槽位中保留上传素材路径和来源标记。
- 同名文件允许覆盖。
- 不保留 run、version、history。
- 文件是否属于当前 StoryBoard，只由 `srt_storyboard.json` 中的路径决定。
- 文件身份必须绑定到 Dialogue / `srt_id`，不能绑定到当前 Shot / Scene 编号。
- 重新组合 Shot / Scene 时，仍存在的 Dialogue 必须继续引用原素材路径；生成素材继续指向 `Working/`，上传素材继续指向 `assets/`，原视频素材继续指向 `visual/srt_frames/`。
- 任何 `Working/` 文件只要要从当前槽位或当前 StoryBoard 中物理移除，都必须先移动或复制到 `assets/history/`。
- history 是 `Working/` 的回收站；永久删除只能发生在 history 中，由用户手动确认。
- 04-02 默认不删除 `Working/` 里的文件；它只创建目录和规划路径。
- 孤儿文件清理必须由单独清理工具或用户确认后执行。

## 文件命名规则

所有 Dialogue 的生成素材文件必须使用：

```text
{dialogue_asset_key}_{AssetType}_{Name}.{ext}
```

其中：

- `dialogue_asset_key` 是稳定的 Dialogue 素材键，优先由 `srt_id` 规范化得到，例如 `srt_0001_02`。
- 如果某条 Dialogue 没有原始 `srt_id`，必须生成稳定的 `dialogue_id`，例如 `dialogue_0007`，并把它写入 `srt_storyboard.json`；不能使用当前 `shot_id` / `scene_id` 代替。
- `AssetType` 只能是 `Audio`、`Image`、`Video`。
- `Name` 用于表达素材槽位。

推荐固定命名：

```text
srt_0001_02_Audio_Final.wav
srt_0001_02_Image_01.png
srt_0001_02_Image_02.png
srt_0001_02_Video_Final.mp4
```

每个 Dialogue 的素材槽位目标：

- 1 个声音槽位：`Audio_Final`，可以绑定上传音频，也可以指向生成音频。
- 1 到 2 个图片槽位：`Image_01`、`Image_02`，可以绑定原视频图片 / 上传图片，也可以指向生成图片。
- 1 个视频槽位：`Video_Final`，可以绑定上传视频，也可以指向生成视频。

图片相关字段分为三类：

- 原视频图片绑定：`dialogue.image_path`、`key_frame_paths[]` 或 `working_assets.images[]` 可以直接引用 `SessionOutput/visual/srt_frames/...`，不拷贝进 `Working/`。
- 上传图片绑定：`working_assets.images[]` 可以引用 `SessionOutput/storyboard/assets/images/...`，只做绑定标记，不拷贝进 `Working/`。
- 新生成图片槽位：`working_assets.images[]` 引用生成工具产出的图片，必须物化到 `SessionOutput/storyboard/Working/{dialogue_asset_key}_Image_XX.{ext}`。

音频和视频槽位同样按来源区分：

- 原视频音频 / 原视频视频：如果原视频素材池提供原始音频或视频片段，只绑定其原始素材路径，`source_type = "original"`，不拷贝进 `Working/`。
- 上传音频 / 上传视频：只绑定 `SessionOutput/storyboard/assets/audios/...` 或 `assets/videos/...`，不拷贝进 `Working/`。
- 新生成音频 / 新生成视频：写入 `Working/{dialogue_asset_key}_Audio_Final.*` 或 `Working/{dialogue_asset_key}_Video_Final.*`。

也就是说：原视频素材和上传素材不进 `Working`，无论它们被绑定到图片、音频还是视频槽位；新生成的声音、图片和视频进入 `Working`。

槽位名是稳定契约，真实文件扩展名由生成工具或上传文件决定。

允许的常见扩展名：

- 声音：`.wav`、`.m4a`、`.mp3`
- 图片：`.png`、`.jpg`、`.jpeg`、`.webp`
- 视频：`.mp4`、`.mov`

工具和 UI 不应假设固定扩展名；必须读取 `srt_storyboard.json` 中的实际 `path`。

## `srt_storyboard.json` 中的素材字段

每个 Dialogue item 应包含：

```json
{
  "dialogue_id": "dialogue_srt_0001_02",
  "srt_id": "srt_0001_02",
  "dialogue_asset_key": "srt_0001_02",
  "dialogue": "已经喝了快3个礼拜了啊",
  "image_path": "",
  "working_assets": {
    "audio": {
      "slot": "Audio_Final",
      "source_type": "",
      "path": ""
    },
    "images": [
      {
        "slot": "Image_01",
        "source_type": "",
        "path": ""
      },
      {
        "slot": "Image_02",
        "source_type": "",
        "path": ""
      }
    ],
    "video": {
      "slot": "Video_Final",
      "source_type": "",
      "path": ""
    }
  }
}
```

Scene 可以保留聚合字段，用于 UI 展示或快速判断该 Scene 下有哪些素材，但这些聚合字段不是素材所有权来源。保存时必须以 Dialogue item 上的 `working_assets` 为准。

```json
{
  "scene_id": "scene_001",
  "dialogue_asset_keys": [
    "srt_0001_01",
    "srt_0001_02"
  ]
}
```

`dialogue_asset_key` 是当前 Dialogue 最终素材的文件名前缀，由稳定的 `srt_id` 或 `dialogue_id` 组成。

`path` 默认为空字符串，表示该槽位还没有最终素材。

当素材生成或从素材池绑定后，工具或 UI 将真实路径写回 `path`，并必须记录来源类型：

- `source_type = "original"`：原视频素材，图片路径通常指向 `SessionOutput/visual/srt_frames/...`，音频 / 视频路径指向原视频素材池或原始片段路径，不进 `Working`。
- `source_type = "upload"`：上传素材，路径指向 `SessionOutput/storyboard/assets/...`，不进 `Working`。
- `source_type = "generated"`：新生成素材，路径指向 `SessionOutput/storyboard/Working/...`。
- `source_type = "history"` 只用于素材池展示；从 history 恢复到当前槽位时，必须复制回 `Working/`，并写为 `source_type = "generated"`，不能让当前槽位直接引用 history 文件。

```json
{
  "slot": "Image_01",
  "source_type": "generated",
  "path": "SessionOutput/storyboard/Working/srt_0001_02_Image_01.jpg"
}
```

`slot` 不包含扩展名；`path` 必须包含真实文件扩展名。

### 当前槽位路径契约

`srt_storyboard.json` 和 `koubo_storyboard_edit.json` 都属于 StoryBoard 长期状态文件。两者可以结构不同，但同一个 Dialogue 的当前素材槽位必须引用同一类稳定业务资产，不能引用任何一次工具执行的临时文件。

允许写入当前槽位的路径：

- `SessionOutput/storyboard/Working/{dialogue_asset_key}_Audio_Final.*`
- `SessionOutput/storyboard/Working/{dialogue_asset_key}_Image_Source.*`
- `SessionOutput/storyboard/Working/{dialogue_asset_key}_Image_New.*`
- `SessionOutput/storyboard/Working/{dialogue_asset_key}_Video_Final.*`
- `SessionOutput/storyboard/assets/audios/...`
- `SessionOutput/storyboard/assets/images/...`
- `SessionOutput/storyboard/assets/videos/...`
- 原视频参考帧或原始素材池路径，例如 `SessionOutput/visual/srt_frames/...`

禁止写入当前槽位的路径：

- `S*_*/Working/*`
- `S*_*/Output/*`
- `S9_05_02_VideoPlanExecutor/Working/*_DialogueAudio.*`
- `S9_05_02_VideoPlanExecutor/Working/*_SegmentAudio_Final.*`
- `S12_05_05_VideoOnlyPlanGenerator/Working/*`
- 任意具体工具目录下的临时 `Working/`、`Output/`、`Prompt/`、`Report/` 文件。

注意：`SessionOutput/storyboard/Working/` 虽然名字也叫 `Working`，但它是 StoryBoard 的标准业务资产槽位目录，可以作为长期引用。`S9_05_02_VideoPlanExecutor/Working/` 这类目录才是工具执行临时目录，只能作为工具内部输入、缓存或中间产物，不能反写进 StoryBoard 当前槽位。

音频必须区分 Dialogue 级和 Segment 级：

- `Audio_Final` 是 Dialogue 级当前音频槽位，可以写入 StoryBoard。
- `DialogueAudio` 是执行器为了合成 Segment 音频复制出来的临时输入，不能写入 StoryBoard。
- `SegmentAudio_Final` 是 Segment 级合并音频，只能作为 Video Plan / Executor 输出或后续拼接输入，不能覆盖任何 Dialogue 的 `working_assets.audio.path`。

图片和视频也遵守同一原则：工具可以在自身 `Working/` 中生成或转换中间文件，但只有发布到 `SessionOutput/storyboard/Working/` 或素材池后的稳定路径，才允许写回 `working_assets`。如果 `srt_storyboard.json` 和 `koubo_storyboard_edit.json` 中同一 Dialogue 的 `Audio_Final`、`Image_New` 或 `Video_Final` 路径不一致，必须视为状态不一致，而不是由 UI 自行猜测修正。

### 槽位状态颜色与绑定优先级

StoryBoard、VideoPlan、ImagePlan 和 VideoOnlyPlan 的槽位颜色必须遵守同一套优先级，避免运行态或失败态覆盖已落盘事实：

1. 标准业务文件已经存在且非空时，对应媒体步骤优先显示绿色；旧 execution state 的 `running` / `failed` 不能把已落盘文件覆盖成黄色或红色。
2. 只有当前执行任务仍在运行，且该步骤标准业务文件尚未落盘时，才显示黄色。
3. 只有标准业务文件不存在，并且当前 execution state 明确失败时，才显示红色。
4. 白色表示前置条件已经满足、用户可以执行；灰色表示该步骤不执行、被跳过或 blocked。
5. 对需要写回 StoryBoard JSON 的槽位，完整完成态必须同时满足“文件存在”和“`srt_storyboard.json` / `koubo_storyboard_edit.json` 绑定一致”。文件已落盘但绑定缺失时，不能显示为最终完成，只能提示补绑定或刷新。

### Final Video 与 TailFrame 成对契约

`TailFrame` 是 `Video_Final` 的下游消费凭证，不是独立可随意复用的图片素材。凡是某个 Dialogue / Segment 的 `Video_Final` 被系统确认为当前最终视频，都必须保证同一 `dialogue_asset_key` 下存在标准尾帧：

```text
SessionOutput/storyboard/Working/{dialogue_asset_key}_Video_Final.mp4
SessionOutput/storyboard/Working/{dialogue_asset_key}_TailFrame.png
```

触发场景包括：

1. `05_02` 生成新视频并完成对口型后，必须从对口型后的 `Video_Final` 抽取 `TailFrame.png`。
2. `05_02` 对空镜 / 产品特写 / 关闭对口型的 segment 做音频替换或重定时后，必须从同步后的 `Video_Final` 抽取 `TailFrame.png`。
3. `05_06` 生成 Raw Video 后可以抽取诊断 / 预览尾帧，但该尾帧不能解锁下游，直到 Raw 被确认或 Sync 成为 `Video_Final`。
4. 用户在 Video Only Plan 中点击 Confirm Final，将 Raw 拷贝为 `Video_Final` 时，必须从新的 `Video_Final` 抽取 `TailFrame.png`。
5. 如果 `Video_Final` 文件已存在但 StoryBoard JSON 只是补绑定，或用户把上传 / 历史视频绑定为当前 `Video_Final`，系统也必须检查并补齐对应 `TailFrame.png`；缺失或过期时重新从当前 `Video_Final` 抽取。

下游只能消费与当前 `Video_Final` 同一 `dialogue_asset_key`、同一最终视频版本匹配的 `TailFrame.png`。如果 `Video_Final` 被替换、删除、解绑或移入 history，对应 `TailFrame.png` 必须同步失效：旧 generated TailFrame 进入 history，上传 / 原始视频绑定解除时不能继续保留旧 generated TailFrame 驱动下游。

对嘴型失败后的 Raw 尾帧只能作为诊断产物记录，不得让后续 `previous_segment_tail_frame` / `previous_scene_tail_frame` 变为可执行。只有 `Video_Final` 成功存在，并且 `TailFrame.png` 由该 Final 抽取成功后，下游首帧依赖才允许显示为可消费。

### Video Only Plan 尾帧物化契约

Video Only Plan 中如果某个片段的首帧来源是 `previous_segment_tail_frame` 或 `previous_scene_tail_frame`，该首帧不是普通新图生成任务，而是上游尾帧依赖。

状态优先级必须按尾帧依赖判断：

1. 上游 TailFrame 文件不存在时，即使本片段已经有 Raw / Final Video，首帧步骤也必须显示白色等待，含义是“等待上一段 / 上一 Shot 尾帧”，不能显示灰色跳过。
2. 上游 TailFrame 文件已存在，但本片段 `Image_New` 尚未存在时，首帧步骤显示白色可执行，含义是“可物化尾帧为新图”。
3. 用户在 Video Only Plan 中点击该“尾帧”步骤时，系统必须把上游 TailFrame 复制到本片段标准新图槽位：`SessionOutput/storyboard/Working/{dialogue_asset_key}_Image_New.*`，并同步写回 `srt_storyboard.json` 与 `koubo_storyboard_edit.json` 的同一 Dialogue：
   - `working_assets.images[0].slot = "Image_New"`
   - `working_assets.images[0].source_type = "tail_frame_materialized"`
   - `working_assets.images[0].path = "SessionOutput/storyboard/Working/{dialogue_asset_key}_Image_New.*"`
   - `bound_image_path` 指向同一个路径。
4. 物化后的 `Image_New` 视为业务当前新图，后续 Video Prompt / Raw Video / Final Video 都读取该标准槽位；不得把上游 TailFrame 路径直接写成当前 `Image_New` 槽位，也不得写入具体工具目录下的临时文件。

### Dialogue 级素材绑定

`working_assets.images[]` 是 Dialogue 级最终图片槽位，表示该 Dialogue 的最终视觉素材。它不能被当成 Scene 级公共图片槽，也不能自动扩散到同 Scene 的其它 Dialogue。

如果 UI 支持给单条 Dialogue 拖入图片，必须写入该 Dialogue 自己的 `working_assets.images[]` 或兼容字段，例如：

```json
{
  "dialogue_id": "scene_001_dialogue_001",
  "srt_id": "srt_0001_01",
  "dialogue_asset_key": "srt_0001_01",
  "working_assets": {
    "images": [
      {
        "slot": "Image_01",
        "source_type": "generated",
        "path": "SessionOutput/storyboard/Working/srt_0001_01_Image_01.jpg"
      }
    ]
  }
}
```

Dialogue 级素材绑定规则：

- 拖图片到某一条 Dialogue 时，只能更新该 Dialogue 的 `working_assets.images[]` 或兼容的 `bound_image_path`。
- 拖视频到某一条 Dialogue 时，只能更新该 Dialogue 的 `working_assets.video.path`。
- 拖声音到某一条 Dialogue 时，只能更新该 Dialogue 的 `working_assets.audio.path`。
- 从原视频素材拖入时，`source_type` 为 `original`，路径保留原视频参考帧路径，不进 `Working`。
- 从上传素材拖入时，`source_type` 为 `upload`，路径保留上传素材池路径，不进 `Working`。
- 从生成工具产出时，`source_type` 为 `generated`，路径写入 `Working`。
- 不得同时写入当前 Scene 的公共 `working_assets`。
- 保存 StoryBoard 时，不得把某条 Dialogue 的素材自动回填到同 Scene 下所有 Dialogue。
- 清空某条 Dialogue 素材时，只能清空该 Dialogue 对应槽位，不能清空同 Scene 的其它 Dialogue。

兼容要求：旧字段 `dialogue.bound_image_path` 可以继续作为 Dialogue 图片槽的兼容读写字段，但新实现必须把 Dialogue 自己的 `working_assets.images[]` 作为主契约。不得再把 `bound_image_path` 同步到 Scene 级图片槽。

已踩坑：口播 StoryBoard 曾经在拖图片到某条 Dialogue 时，同时写了 `dialogue.bound_image_path` 和 `scene.working_assets.images[0].path`。后端保存时又把 `working_assets.images[0].path` 当成 Scene 图片回填到该 Scene 下所有 Dialogue，结果保存后整个 Scene 的 Dialogue 都显示成用户拖给第一条 Dialogue 的图片。修复要求是前端和后端都以 Dialogue / `srt_id` 为素材所有权边界，不允许把任何单条 Dialogue 素材扩散为 Scene 素材。

## 选中、生成和播放规则

StoryBoard UI 的播放粒度是 Scene，用户选择范围决定本次播放队列。选择状态、播放队列和播放中高亮必须分离，播放过程不应改写用户原本选择的范围。

### 选择范围

UI 必须维护一个明确的选择范围：

- `scene`：播放当前选中的 Scene。
- `shot`：播放当前选中 Shot 下的全部 Scene。
- `all`：播放当前 StoryBoard 的全部 Scene。

选择行为：

- 点击 Scene bar 或 Scene / Dialogue 卡片时，选择范围为 `scene`。
- 点击 Shot bar 或 Shot 节点时，选择范围为 `shot`，并清空当前 Scene 选择。
- 点击 Timeline 空白区域时，选择范围为 `all`，并清空当前 Scene 选择。
- 如果 UI 的编辑选择停留在 Dialogue 层，播放前必须先把 Dialogue 映射到其所在 Scene，再按 Scene 播放。

实现要求：所有入口必须同步维护同一组选中状态。左侧树、Timeline、Shot / Scene / Dialogue 卡片不能各自只更新视觉选中态，否则会出现 UI 看起来选中了某个 Scene，但播放队列仍沿用旧 `all` 或 `shot` 范围的问题。

口播 StoryBoard 修正规则：

- 左侧点击 Shot 节点时，必须设置当前 Shot、清空当前 Dialogue / Scene 选择，并把范围设置为 `shot`。
- 左侧点击 Scene 节点时，必须设置当前 Shot、选择该 Scene 的第一条 Dialogue，并把范围设置为 `scene`。
- 左侧点击 Dialogue 节点时，必须设置当前 Shot、选择该 Dialogue，并把范围设置为 `scene`。
- 中间卡片和 Timeline 的同类点击行为必须与左侧树保持一致。
- 任何播放队列生成函数不得只依赖“最后一次 scope 值”，必须保证 scope 已由当前交互入口更新。
- Scene 行应提供明确的 `生成TTS` / `播放TTS` 控件。用户点击该控件时，UI 必须立即把选择范围切换为当前 Scene，并只对该 Scene 执行 TTS 生成、播放和时长回写。

已发现的问题：口播 StoryBoard 曾经在左侧树点击 Shot / Scene / Dialogue 时只更新 `selectedShotIndex` 和 `selectedDialogueId`，没有同步更新 `scope`。如果用户之前点击过 Timeline 空白区域导致 `scope = all`，再从左侧树选中某个 Scene 后点击播放，实际会按全片队列生成 TTS，而不是按当前 Scene 生成。

### 播放队列

用户点击播放按钮时，UI 必须基于当时的选择状态生成一次固定队列：

- `scene` 队列只包含当前 Scene。
- `shot` 队列包含当前 Shot 内按顺序排列的 Scene。
- `all` 队列包含当前 StoryBoard 内按 Shot / Scene 顺序排列的全部 Scene。

本次播放开始后，队列不得因为播放中高亮、滚动定位或后续状态更新而被重新解释。播放过程中可以更新：

- `playbackCurrentShotId`
- `playbackCurrentSceneId`
- 当前播放进度，例如 `1/3`
- 当前阶段，例如 `generating`、`playing`、`paused`、`idle`、`error`

播放过程中不应把用户的选择范围强制改成 `scene`。例如用户选择 `all` 后点击播放，播放第一个 Scene 时可以高亮当前 Scene，但底部 Timeline 仍应保留全片选择态。

### Dialogue TTS 生成和缓存

每个 Dialogue 播放前必须先取得该 Dialogue 对应的最终音频。Scene 播放是按当前 Scene 内 Dialogue 顺序逐条生成 / 播放，而不是把 Scene 当成一个素材所有者。

1. 优先使用内存中配置一致的 Dialogue 音频状态。
2. 其次读取对应 locked manifest，且 manifest 中的 `config_key` 必须与当前 Dialogue 文本和 TTS 设置一致。
3. 如果没有可复用音频，才允许调用 TTS 生成接口。

TTS 配置一致性至少应包含：

- Dialogue ID。
- SRT ID。
- `dialogue_asset_key`。
- Scene ID。
- Dialogue 当前文本。
- TTS Provider / Model / Voice。
- Prompt 展开后的真实朗读指令。
- Tempo 或等价语速参数。

生成成功后，最终音频必须写入：

```text
SessionOutput/storyboard/Working/{dialogue_asset_key}_Audio_Final.{ext}
```

同时必须更新当前 Dialogue item 的：

```json
{
  "dialogue_id": "dialogue_srt_0001_02",
  "srt_id": "srt_0001_02",
  "dialogue_asset_key": "srt_0001_02",
  "working_assets": {
    "audio": {
      "slot": "Audio_Final",
      "path": "SessionOutput/storyboard/Working/srt_0001_02_Audio_Final.{ext}"
    }
  }
}
```

播放使用的浏览器 `Audio.src` 必须和当前 Dialogue 的 `working_assets.audio.path` 指向同一个文件，不能用 Scene ID、数组下标、上一次播放缓存、底部 Timeline clip ID 或其它临时 ID 拼接。播放状态里可以有 UI 用的 scene / shot 高亮 ID，但真实音频源的稳定身份必须来自当前 Dialogue 的 `dialogue_asset_key` 和 `working_assets.audio.path`。

locked manifest 应记录实际输出、TTS 配置和 `config_key`，供下次播放复用。

Dialogue TTS 的输入文本必须来自当前 Dialogue 自身的 SRT 文本，不允许使用 Builder-G sample、全片文本、Shot 文本、Scene 拼接文本或上一次播放缓存文本替代。实现上应先由当前播放队列拿到 Dialogue item，再把该 Dialogue 的文本作为本次 TTS 的唯一朗读正文。

### TTS Prompt 与正文隔离

TTS 请求中必须严格区分“风格指令”和“当前 Dialogue 正文”。Builder-G 候选、推荐声音、历史音频、试听样本里可能携带旧的正文片段，这些内容只能作为风格参考，不能作为本次 Dialogue TTS 的朗读正文。

实现要求：

- 当前 Dialogue 正文只能来自该 Dialogue / SRT 文本。
- 保存到 `storyboard_tts_selection.prompt` 的 prompt 不能被视为可信正文来源。
- 如果 prompt 中包含 `朗读文本:`、`正文:`、`Text:` 这类正文标记，生成 TTS 前必须移除标记之后的旧正文。
- 移除旧正文后，再把当前 Dialogue 正文显式注入到 provider 需要的字段或 prompt 中。
- `config_key` 必须使用清理并注入当前 Dialogue 正文后的最终 prompt，而不是原始保存 prompt。

不同 Provider 的规则：

- Google / Gemini TTS：接口主要从 `contents.parts.text` 中理解朗读内容，因此最终发送给 Google 的 prompt 必须是“清理后的风格指令 + 当前 Dialogue 正文”。如果直接把保存的 prompt 发送给 Google，而 prompt 里带有旧 `正文:`，Google 会朗读旧正文。
- Qwen instruct TTS：`text` 字段必须填当前 Dialogue 正文，`instructions` 只能放清理后的风格指令，不能包含旧 `朗读文本:` 或历史正文。
- xAI TTS：正文使用 `text` 字段，prompt 不应覆盖当前 Dialogue 正文。

已踩坑：口播 StoryBoard 中选择了 Google `gemini-3.1-flash-tts` 后，界面虽然选中了当前 Dialogue / Scene，但生成结果仍朗读“图2”的内容。根因不是模型选择错误，而是 Builder-G 推荐 prompt 里保留了旧样本文本，例如 `正文:` / `朗读文本:` 后面的历史内容。Google TTS 使用 prompt 作为主要输入，导致它朗读了旧 prompt 正文，而不是当前 Dialogue 对白。修复方式是前端和后端都清理 prompt 中的旧正文，并强制注入当前 Dialogue 文本。

### 重新生成判定

Dialogue TTS 是否复用缓存，只能由 `config_key` 决定。只要影响音频内容、音色或语速的输入发生变化，就必须重新生成 TTS。

`config_key` 必须至少包含：

- `dialogueId`：当前 Dialogue ID。
- `srtId`：当前 SRT ID。
- `dialogueAssetKey`：当前 Dialogue 素材键。
- `sceneId`：当前 Scene ID。
- `text`：当前 Dialogue / SRT 正文。
- `provider`：TTS Provider。
- `model`：TTS Model。
- `voiceId`：TTS Voice。
- `prompt`：清理旧正文并注入当前 Dialogue 正文后的最终 prompt。
- `tempo`：TTS tempo / speed factor。

必须重新生成的情况：

- 当前 Dialogue ID、SRT ID 或 `dialogue_asset_key` 变化。
- 当前 Dialogue 文本变化。
- Provider 变化。
- Model 变化，例如从一个 Gemini TTS 模型切到另一个模型。
- Voice 变化。
- Tempo 变化。
- Prompt 风格指令变化。
- prompt 清理或正文注入逻辑变化，导致最终 prompt 变化。

允许复用缓存的情况：

- `config_key` 完全一致。
- locked manifest 存在，manifest 中的 `config_key` 与当前 `config_key` 完全一致。
- manifest 指向的音频文件存在且非空。

不应触发重新生成的情况：

- 只改变 UI 选中态或播放高亮，但 Dialogue 文本、TTS 设置和最终 prompt 没变。
- 只改变手动 Scene / Dialogue duration，且当前 TTS 不启用 `fit_to_duration`。如果未来支持按目标时长适配音频，则目标时长也必须加入 `config_key`。
- 只把 Dialogue 移动到其它 Shot / Scene，且 Dialogue 文本、TTS 设置和最终 prompt 没变。

缓存层级：

1. 前端内存缓存：只有 `dialogueAudioState[dialogueAssetKey].configKey === currentConfigKey` 时可复用。
2. locked manifest：只有 `manifest.config_key === currentConfigKey` 时可复用。
3. 以上任一不匹配，必须调用 TTS 生成接口。

注意：不能只用输出文件路径判断是否已有音频。路径相同但 `config_key` 不同，表示模型、tempo、文本、prompt 或 Dialogue 已变化，必须覆盖生成新音频。

口播 StoryBoard 修正规则：

- `scene` 队列只允许按当前 Scene 内 Dialogue 顺序逐条调用 `dialogueText(dialogue)`。
- `shot` 队列必须逐 Scene、逐 Dialogue 调用 TTS，不能把整个 Shot 的文本一次性生成。
- `all` 队列必须逐 Scene、逐 Dialogue 顺序生成 / 播放，不能把全片文本一次性生成。
- `config_key` 必须包含 Dialogue ID、SRT ID、`dialogue_asset_key`、当前 Dialogue 文本、TTS Provider / Model / Voice、展开后的 Prompt 和 Tempo。
- 如果 Dialogue 文本、Dialogue 身份或 TTS 设置变化，必须绕过旧 locked cache 重新生成。

已发现的问题：口播 StoryBoard 的单个 Scene TTS 文本拼接逻辑是 Scene 级的，但因为左侧树选中没有同步 `scope`，播放队列可能不是用户当前选中的 Scene，最终表现为“选中某个 Scene 后却生成了其它 Scene 或全片范围的 TTS”。

### TTS 时长回写

Timeline 播放代表当前 Dialogue 音频的顺序播放结果，因此每条 Dialogue 音频播放完成后，UI 必须用实际音频时长更新当前 StoryBoard 时间信息。

更新规则：

- 读取 TTS 返回的 `duration_seconds`；如果缺失，则使用浏览器 `Audio` 播放结束时的 duration。
- 将该 Dialogue 的总时长更新为音频时长。
- 当前 Dialogue 必须更新 `duration`、`start`、`end`。
- 当前 Dialogue 应记录 `tts_duration`。
- 当前 Dialogue 的 `timing.source` 应标记为 `storyboard_tts_audio`，并记录 `audio_dialogue_id` 和 `audio_srt_id`。
- 更新 Dialogue 后必须重算 Scene、Shot 和 StoryBoard 总时长。
- 更新后的时间必须进入当前可保存的 StoryBoard plan，不能只存在播放状态里。

已发现的问题：口播 StoryBoard 曾经只生成并播放音频，然后把音频路径写入 `working_assets.audio.path`，但没有用音频实际时长更新 Dialogue / Scene / Shot 的时间。这样会导致 Timeline 显示、后续视频生成和最终音频长度不一致。

### 播放控制

播放按钮行为：

- `idle` 时点击：按当前选择生成队列并开始播放。
- `generating` 时点击：取消本次播放流程，停止后续 Scene 生成或播放。
- `playing` 时点击：暂停当前音频，状态变为 `paused`。
- `paused` 时点击：继续当前音频，状态恢复为 `playing`。
- 队列结束：清空播放态，保留用户播放前的选择范围。
- 发生错误：进入 `error` 状态并展示错误信息，不允许卡在 `generating` 或 `playing`。

Builder-G 候选声音只可用于 TTS 设置里的 voice sample 试听，不应在 Timeline 播放中静默替代当前 Dialogue 的最终 TTS。Timeline 播放必须代表当前 Dialogue 文本生成或缓存得到的 Dialogue 音频。

### 完整播放流程

Dialogue 行 `生成TTS` / `播放TTS` 按钮流程：

1. 用户点击某个 Dialogue 行的按钮。
2. UI 立即设置当前 Shot、当前 Scene、当前 Dialogue，并把范围设为 `scene`。
3. 如果当前 Dialogue 正在播放，则暂停；如果当前 Dialogue 已暂停，则继续播放。
4. 如果当前 Dialogue 正在生成，则忽略重复点击或保持生成态。
5. 如果不是当前 Dialogue，先停止旧播放流程，递增 run id，防止旧异步流程继续写状态。
6. 读取当前 Dialogue / SRT 文本。
7. 读取当前 TTS Provider / Model / Voice / Tempo / Prompt 设置。
8. 清理 Prompt 中的旧正文，并注入当前 Dialogue 文本。
9. 计算当前 `config_key`。
10. 先查前端内存缓存，`config_key` 一致才复用。
11. 再查 locked manifest，`config_key` 一致且音频文件存在才复用。
12. 缓存未命中时调用 Dialogue TTS 接口生成。
13. 生成成功后写入 `Working/{dialogue_asset_key}_Audio_Final.{ext}`。
14. 写入 locked manifest 和当前 Dialogue 的 `working_assets.audio.path`。
15. 播放生成或缓存得到的 Dialogue 音频。
16. 播放结束后用音频真实时长回写 Dialogue / Scene / Shot 时间。
17. 清空播放态，保留用户对该 Dialogue 所在 Scene 的选择。

Timeline 播放按钮流程：

1. `idle` 时根据当前 `scope` 固定生成队列。
2. `scene` 队列只包含当前 Scene。
3. `shot` 队列包含当前 Shot 下所有 Scene。
4. `all` 队列包含全部 Scene。
5. 队列中的每个 Scene 都按 Dialogue 顺序执行“Dialogue 行按钮流程”的生成、缓存、播放、时长回写规则。
6. 播放中只更新 `playbackCurrentShotId`、`playbackCurrentSceneId` 和进度，不改写用户原本选择范围。
7. `playing` 点击为暂停当前音频。
8. `paused` 点击为恢复当前音频。
9. `generating` 点击为取消当前队列，后续 Scene 不再生成或播放。
10. 队列结束后清空播放态。

## `dialogue_asset_key` 生成规则

`dialogue_asset_key` 必须由 04-02 工具生成，不应直接信任模型输出。它是素材文件名前缀，也是素材跟随 Dialogue 移动的稳定身份。

生成规则：

```text
dialogue_asset_key = normalized(srt_id || dialogue_id)
```

UI 在手动拆分、合并、重排 Shot / Scene 后，不得因为 `shot_id` / `scene_id` 变化而重新计算仍存在 Dialogue 的 `dialogue_asset_key`。只要 Dialogue 仍存在，`dialogue_asset_key`、`Working/` 路径和 locked manifest 仍应保持稳定。

口播 StoryBoard 修正规则：

- 前端每次 renumber / regroup plan 时必须保留原 Dialogue 的 `dialogue_asset_key`。
- `splitScene` 只是改变 Dialogue 所在 Scene，不改变原 Dialogue 的 `dialogue_asset_key`。
- `splitShot` 只是改变 Scene 所在 Shot，不改变原 Dialogue 的 `dialogue_asset_key`。
- `mergeScene` / `mergeShot` 只改变层级，不改变仍存在 Dialogue 的 `dialogue_asset_key`。
- `mergeDialogue` 时，保留下来的前一个 Dialogue 继续使用自己的 `dialogue_asset_key`；被合并后消失的 Dialogue，其素材进入 history。
- `deleteDialogue` 时，该 Dialogue 的素材进入 history，当前 StoryBoard 不再引用。
- TTS locked path 必须基于当前 Dialogue 的 `dialogue_asset_key`。
- 如果当前 Dialogue 没有可用 `dialogue_asset_key`，TTS 生成应拒绝复用 locked cache，或先生成唯一 `dialogue_asset_key` 后再写入。

已发现的问题：口播 StoryBoard 曾经用 `{shot_id}_{scene_id}` 作为素材前缀。这样一旦用户重新组合 Shot / Scene，同一条 Dialogue 的素材路径会被迫变化，或者两个 Scene 复制出相同前缀，导致 TTS locked output 和素材槽位互相覆盖。修正后，素材前缀必须绑定 Dialogue / `srt_id`，Shot / Scene 重排不能改变素材身份。

要求：

- `shot_id` 使用 `shot_001`、`shot_002` 这样的全局 Shot 顺序编号。
- `scene_id` 使用 `scene_001`、`scene_002` 这样的全局 Scene 顺序编号。
- Scene 编号在整个 StoryBoard 内全局递增，不在每个 Shot 内重新从 1 开始。
- `dialogue_asset_key` 在当前 `srt_storyboard.json` 内必须唯一。
- `dialogue_asset_key` 不能包含当前 `shot_id` / `scene_id`。
- 如果模型输出了重复或不规范的 `shot_id` / `scene_id`，04-02 应按顺序重写为规范 ID。
- 如果模型输出了重复或不规范的 `dialogue_id` / `srt_id`，04-02 应为对应 Dialogue 生成稳定唯一的 `dialogue_asset_key`。

示例：

```text
srt_0001_01
srt_0001_02
dialogue_0007
```

## 图片引用规则

04-02 回填 StoryBoard 时：

- 每个 Scene 只有第一句 `dialogue_items[0]` 保留 `image_path`。
- 同一个 Scene 后续 dialogue 的 `image_path` 必须为空字符串。
- `key_frame_paths` 只保留该 Scene 第一句的参考图。

抽帧参考图仍保存在：

```text
SessionOutput/visual/srt_frames/
```

这些参考图是原图参考，不是 `Working/` 新素材，不应复制到：

```text
SessionOutput/storyboard/Working/
```

如果某条 Dialogue 只是展示原图参考，路径保留在 `dialogue.image_path` 或 `key_frame_paths[]`。用户把原视频素材或上传素材拖入图片槽时，只写入 `working_assets.images[]` 的绑定路径和 `source_type`，不物化到 `Working/`。只有生成工具产出新图时，才写入 `Working/`。

## 参考帧和原画面绑定

02-02 会把每条 SRT / Dialogue 绑定到视频原画面参考帧，参考帧文件保存在：

```text
SessionOutput/visual/srt_frames/
```

常见文件名：

```text
SessionOutput/visual/srt_frames/srt_0001.jpg
SessionOutput/visual/srt_frames/srt_0001_01.jpg
SessionOutput/visual/srt_frames/srt_0001_02.jpg
```

02-02 同时会生成帧绑定索引：

```text
SessionOutput/visual/srt_frame_map.json
SessionOutput/subtitle/final_srt_frame_items.json
SessionOutput/subtitle/rewritten_srt_items.json
```

绑定链路：

```text
srt_id
  -> SessionOutput/subtitle/final_srt_frame_items.json[].image_path
  -> SessionOutput/subtitle/rewritten_srt_items.json[].image_path
  -> SessionOutput/storyboard/srt_storyboard.json[].shots[].scenes[].dialogue_items[].image_path
```

04-02 不重新抽帧，也不重新选择图片。它只从 `rewritten_srt_items.json` 继承 `image_path`，并按 Scene 规则写入 StoryBoard：

- 每个 Scene 的第一条 dialogue item 保留 `image_path`。
- 同一个 Scene 的后续 dialogue item 将 `image_path` 写为空字符串。
- 每个 Scene 的 `key_frame_paths` 只保留该 Scene 第一条 dialogue item 的 `image_path`。

因此，参考帧有两个用途：

- 给 UI 展示该 Scene 来源于原视频的哪一帧。
- 给后续人工或 AI 素材生成提供视觉参考。

参考帧不是 `Working/` 新素材，不应复制或移动到 `Working/`。如果 UI 只是显示原图参考，则继续使用 `dialogue.image_path` / `key_frame_paths[]` 的原路径。

如果用户把原视频素材或上传素材拖拽放入 Dialogue 的图片、音频或视频槽位，只做绑定标记，`path` 指向原视频参考帧或上传素材池文件，不复制到 `Working/`。删除这类绑定时，只清空槽位，素材回到原视频素材池或上传素材池可用状态。

如果生成工具产出新图片、新音频或新视频，则写入 `Working/{dialogue_asset_key}_Image_XX.*`、`Working/{dialogue_asset_key}_Audio_Final.*` 或 `Working/{dialogue_asset_key}_Video_Final.*`。删除这类生成素材时，先进入 history。

如果仍需要兼容旧的 `bound_image_path`，也只能在同一条 Dialogue 内同步。不要为了展示 Dialogue 缩略图而写入 Scene 级 `working_assets.images[].path`，否则保存或物化素材时容易把该图片误认为 Scene 级最终图片并扩散到整个 Scene。

示例：

```json
{
  "srt_id": "srt_0001_01",
  "dialogue": "给家里备这个化橘红啊",
  "image_path": "SessionOutput/visual/srt_frames/srt_0001_01.jpg"
}
```

对应 Scene：

```json
{
  "scene_id": "scene_001",
  "key_frame_paths": [
    "SessionOutput/visual/srt_frames/srt_0001_01.jpg"
  ],
  "dialogue_items": [
    {
      "srt_id": "srt_0001_01",
      "dialogue": "给家里备这个化橘红啊",
      "image_path": "SessionOutput/visual/srt_frames/srt_0001_01.jpg"
    },
    {
      "srt_id": "srt_0001_02",
      "dialogue": "已经喝了快3个礼拜了啊",
      "image_path": ""
    }
  ]
}
```

## 覆盖和清理规则

StoryBoard 不保留版本。

当用户重新生成某个 Dialogue 的声音、图片或视频时：

- `slot` 保持稳定。
- 新生成结果必须写入 `Working/{dialogue_asset_key}_{slot}.{ext}`。
- 如果该槽位旧素材是 `source_type = "generated"`，旧 `Working/` 文件必须先移入 history，再写入新生成文件；不能用直接覆盖绕过 history。
- 如果旧素材是 `source_type = "upload"` 或 `source_type = "original"`，重新生成时只解除旧绑定，不进入 history。
- 如果扩展名不变，当前槽位 `path` 可以保持同一目标路径；但旧文件仍必须先归档到 history 后再写新文件。
- 如果扩展名变化，必须更新 `path` 为新的真实文件名。
- UI 可以通过文件修改时间判断素材是否更新，但不能仅靠同名覆盖作为历史保留机制。

当 Shot / Scene 分组被重新调整时：

- 新的 `srt_storyboard.json` 是唯一有效索引。
- 仍存在 Dialogue 的 `dialogue_asset_key` 和素材路径必须保持不变。
- `Working/` 中不再被当前 `srt_storyboard.json` 引用的文件，不能直接删除；清理时也必须先进入 history。
- UI 默认不展示孤儿文件。
- 后续清理工具可以按当前 `srt_storyboard.json` 将孤儿文件归档到 history。
- 04-02 不应主动删除 `Working/` 里的文件。
- 04-02 不应主动删除用户上传的 `assets/` 素材。

Dialogue 删除 / 合并规则：

- 删除 Dialogue：把该 Dialogue 的 `source_type = "generated"` 素材复制或移动到 `assets/history/{batch_id}/`，manifest 记录 `reason = delete_dialogue`；`upload` / `original` 素材只解除绑定。
- 合并 Dialogue：保留下来的前一个 Dialogue 不改素材、不改 `dialogue_asset_key`；被并入后消失的 Dialogue，只有 `generated` 素材进入 history，manifest 记录 `reason = merge_dialogue_removed` 和 `merged_into_dialogue_id`。
- 删除槽位素材：如果该槽位引用 `generated` 的 `Working/` 文件，则移入 history，manifest 记录 `reason = remove_slot_asset`；如果引用 `upload` 或 `original`，只清空绑定，让素材回到对应素材池。
- 替换槽位素材：如果旧素材是 `generated`，则先把旧文件移入 history，manifest 记录 `reason = replace_slot_asset`，再写入新素材；如果旧素材是 `upload` 或 `original`，只解除绑定。
- 恢复 history 素材：必须复制到当前 Dialogue 对应的 `Working/{dialogue_asset_key}_{slot}.{ext}`，并把当前槽位写为 `source_type = "generated"`。
- 最终删除素材：只允许在 history 里由用户手动删除，manifest 记录 `reason = permanent_delete_from_history` 或移除对应条目。
- 重排 Shot / Scene：不产生 history，不改素材路径。
- 合并 Shot / Scene：只要 Dialogue 仍存在，不产生 history，不改素材路径。

## Computer Use 测试案例：故事版（口播）素材上传、拖拽、保存和删除

本节只适用于“故事版（口播）”页面，不适用于 OC-StoryBoard 通用页面。Task #31 的默认验证入口为：

```text
#/koubo-storyboard/tasks/31
```

测试目标：

- 覆盖 `原视频素材`、`上传素材`、`历史素材` 三类来源。
- 覆盖图片、视频、声音三类素材。
- 覆盖 Dialogue 图片、视频、声音最终素材槽位。
- 覆盖空槽拖入、同类型替换、跨类型误拖、保存刷新、从槽位移除、从素材库删除。
- 确认保存后 `srt_storyboard.json` 或口播编辑态 JSON 中没有错写、串写、残留坏路径。

### 前置数据和页面状态

测试前准备：

- 至少 2 个原视频参考帧素材。
- 至少 2 张上传图片，例如 `.png`、`.jpg` 或 `.webp`。
- 至少 2 个上传视频，例如 `.mp4`、`.mov`。
- 至少 2 个上传声音，例如 `.wav`、`.mp3` 或 `.m4a`。
- 1 个无效文件，例如 `.txt`，用于验证拒绝上传。
- 至少 2 个 Shot、2 个 Scene、每个 Scene 至少 1 条 Dialogue。
- 如果要测试历史素材，先通过删除 Dialogue 或合并 Dialogue 生成历史素材版本；只拆分、合并或重排 Shot / Scene 不应生成历史素材。

页面必须显示：

- 标题或来源信息包含 `故事版（口播）`。
- 左侧 Shot / Scene / Dialogue 树。
- 中间 Timeline Editor。
- 右侧 `Asset Pool`。
- 右侧素材页签：`原视频素材`、`上传素材`、`历史素材`。

### Session Workspace 素材自组织规则

故事版（口播）的右侧素材池必须从当前 Task 对应的 Session Workspace 自行组织，不能要求用户在数据库或前端状态里手工补素材记录。Computer Use 测试时，可以直接把测试素材放入 Session Workspace，再刷新页面验证 UI 是否自动归类。

Workspace 约定：

```text
{session_workspace}/SessionOutput/storyboard/
  assets/
    images/
      upload_image_a.png
      upload_image_b.jpg
    videos/
      upload_video_a.mp4
      upload_video_b.mov
    audios/
      upload_audio_a.wav
      upload_audio_b.mp3
    history/
      batch_20260530_153012_remove_slot_asset/
        manifest.json
        history_image_a.png
        history_video_a.mp4
        history_audio_a.wav
```

自组织要求：

- `原视频素材` 从当前 StoryBoard 的 Dialogue / SRT 参考图路径组织，通常来自 `SessionOutput/visual/srt_frames/`。
- `上传素材` 从 `SessionOutput/storyboard/assets/images/`、`assets/videos/`、`assets/audios/` 扫描组织。
- `历史素材` 从 `SessionOutput/storyboard/assets/history/*/manifest.json` 组织；批次目录内素材文件平铺，不能要求存在 `images/`、`videos/`、`audios/` 子目录。如果 manifest 缺失，UI 或后端可以按目录扫描作为降级，但必须标记来源为 `history`。
- `原视频素材` 是只读参考池，必须能从当前 StoryBoard 的 SRT / Dialogue 参考帧重新组织出来；绑定、删除槽位、Save、刷新、重排、合并或删除 Dialogue 都不能删除或清空原视频素材池本体。
- `原视频素材` 数量必须等于当前有效 SRT / Dialogue 参考帧数量。以 Task #31 当前验收数据为例，应为 42 个原视频参考帧素材；如果 UI 显示为 0 或只剩少量卡片，必须视为回归失败。
- 素材分类以真实文件扩展名和 manifest 中的 `asset_type` 为准；二者冲突时必须优先拒绝或提示异常，不能静默错分。
- 刷新页面或重新进入 Task 后，素材池必须重新从 Workspace 组织出来，不能依赖上一次上传接口返回的内存数据。
- 素材卡必须带有稳定路径、来源、类型和可拖拽状态。
- 上传素材和历史素材不能混在同一个页签里；历史素材必须保留批次分组。
- 无效文件不能进入任何素材池分组。

Workspace 自组织测试案例：

| ID | 操作 | 预期 |
| --- | --- | --- |
| WS-01 | 直接在 Workspace 的 `assets/images/` 放入 2 张图片后刷新页面 | `上传素材` 页签的 `Images` 自动出现 2 张素材 |
| WS-02 | 直接在 Workspace 的 `assets/videos/` 放入 2 个视频后刷新页面 | `上传素材` 页签的 `Videos` 自动出现 2 个素材 |
| WS-03 | 直接在 Workspace 的 `assets/audios/` 放入 2 个声音后刷新页面 | `上传素材` 页签的 `Audios` 自动出现 2 个素材 |
| WS-04 | 直接在 Workspace 的 `assets/history/batch_xxx/manifest.json` 登记图片、视频、声音后刷新页面 | `历史素材` 页签按 batch 分组显示三类素材 |
| WS-04A | history batch 目录内没有 `images/` / `videos/` / `audios/` 子目录，素材文件与 `manifest.json` 同级 | UI 仍能按 manifest 和扩展名正确显示三类历史素材 |
| WS-05 | 历史目录存在文件但 manifest 缺失 | UI 或后端按目录降级扫描，或显示明确异常；不能静默丢素材 |
| WS-06 | manifest 指向不存在的历史文件 | 素材卡显示异常或被过滤，并在证据中记录；不能生成可拖拽坏卡片 |
| WS-07 | 上传素材目录中混入 `.txt` | `.txt` 不显示在 `Images`、`Videos`、`Audios` 中 |
| WS-08 | 同名素材分别存在于上传素材和历史素材 | 两个素材分别显示在各自页签，路径和来源不混淆 |
| WS-09 | Workspace 新增素材后不调用上传接口，只刷新页面 | 新素材仍能出现，证明素材池不是依赖前端内存 |
| WS-10 | 从 Workspace 删除一个未使用上传素材后刷新页面 | 素材卡消失，不影响其它素材 |
| WS-11 | 打开 Task #31 后进入 `原视频素材` 页签 | 原视频素材数量为 42；每个素材路径指向原始参考帧或原始片段，不指向 `Working/` |
| WS-12 | 把一个原视频素材绑定到 Dialogue 图片槽，Save 后刷新 | `原视频素材` 页签数量仍为 42，绑定只写 `source_type = original` 和原始路径 |
| WS-13 | 从槽位删除原视频素材绑定，Save 后刷新 | 只清空当前槽位；`原视频素材` 页签数量仍为 42，素材卡没有被移入 history，也没有被物理删除 |
| WS-14 | 删除或合并一个 Dialogue 后 Save 刷新 | 原视频素材池仍按剩余 StoryBoard / SRT 参考帧重新组织；不能因为解除引用而把全局原视频素材池清空 |

### 素材池页签状态保持测试案例

右侧 `Asset Pool` 页签状态是编辑上下文的一部分。拖拽、上传、删除、Save、刷新或重新进入 Task 后，除非用户主动点击其它页签，否则不能自动回到第一个 `原视频素材` 页签。

实现和测试要求：

- 当前页签必须写入稳定状态，至少包括 URL 参数 `assetPanelTab`；本地缓存只能作为 URL 不可用时的兜底。
- 页签状态必须由编辑器父层和 `AssetPanel` 自身共同兜底，避免右侧栏重挂载后丢失。
- 从素材卡开始拖拽时，必须先确认并保存当前页签，避免拖拽选中素材、素材池刷新或右侧栏重挂载后回落到默认页签。
- 上传区文件拖入时，必须保持在 `上传素材` 页签。
- 刷新页面后，地址栏中的 `assetPanelTab` 必须与 UI 高亮页签一致。

| ID | 操作 | 预期 |
| --- | --- | --- |
| TAB-01 | 点击 `上传素材`，普通刷新浏览器 | 页面仍停留在 `上传素材`，URL 保留 `assetPanelTab=upload` |
| TAB-02 | 点击 `历史素材`，普通刷新浏览器 | 页面仍停留在 `历史素材`，URL 保留 `assetPanelTab=history` |
| TAB-03 | 在 `上传素材` 页签拖拽一张图片到 Dialogue 图片槽 | 拖拽完成后右侧仍停留在 `上传素材`，不跳回 `原视频素材` |
| TAB-04 | 在 `历史素材` 页签拖拽一个历史素材到 Dialogue 槽位 | 拖拽完成后右侧仍停留在 `历史素材`，不跳回 `原视频素材` |
| TAB-05 | 在 `上传素材` 页签拖入文件到上传区 | 上传完成后右侧仍停留在 `上传素材` |
| TAB-06 | 在 `历史素材` 页签删除一个未使用历史素材 | 删除完成后右侧仍停留在 `历史素材` |
| TAB-07 | 切到 `上传素材` 或 `历史素材` 后 Save，再刷新并重新打开 Task #31 | UI 页签、URL 参数和素材列表保持一致 |

### 槽位定义

Computer Use 必须按以下槽位逐一验证，不允许只测一个图片缩略图拖拽。

| 槽位 | 允许素材 | 写入字段 | 拒绝素材 | 删除槽位后的预期 |
| --- | --- | --- | --- | --- |
| Dialogue 原图参考 | 原视频参考帧 | `dialogue.image_path` / `scene.key_frame_paths[]` | 上传图、视频、声音、无效文件 | 原图参考不作为新素材槽删除，不影响 `Working/` |
| Dialogue `Image_01` | 图片 | `dialogue.working_assets.images[0].path` + `source_type` | 视频、声音、无效文件 | 清空该 Dialogue 的 `Image_01`；`generated` 进 history，`upload/original` 回素材池 |
| Dialogue `Image_02` | 图片 | `dialogue.working_assets.images[1].path` + `source_type` | 视频、声音、无效文件 | 清空该 Dialogue 的 `Image_02`；`generated` 进 history，`upload/original` 回素材池 |
| Dialogue `Video_Final` | 视频 | `dialogue.working_assets.video.path` + `source_type`；同时维护同 key `TailFrame.png` | 图片、声音、无效文件 | 清空该 Dialogue 的视频槽；`generated` 进 history，`upload` 回上传素材池；同 key TailFrame 同步失效 |
| Dialogue `Audio_Final` | 声音 | `dialogue.working_assets.audio.path` + `source_type` | 图片、视频、无效文件 | 清空该 Dialogue 的声音槽；`generated` 进 history，`upload` 回上传素材池 |

说明：

- 所有槽位都属于 Dialogue，不能回填到 Scene 公共字段。
- 原视频素材可以绑定到原图参考、图片槽、音频槽或视频槽；统一写 `source_type = "original"`，不复制到 `Working/`。
- 拖图片到 Dialogue 图片槽时，只能写入该 Dialogue 的 `working_assets.images[]` 和 `source_type`；原视频和上传素材不进 `Working`，生成图片才进 `Working`。
- 拖视频或声音到 Dialogue 卡片区域时，只有明确的 Dialogue 视频或声音槽可以接受；如果 UI 没有明确槽位，必须补 UI 后再验收。
- 拖上传视频或上传声音到对应槽位时，只做绑定标记，不进 `Working`；生成视频或生成声音才写入 `Working`。
- Shot / Scene 重排后，上述槽位仍跟随原 Dialogue / `srt_id`。
- 从当前槽位删除 `generated` 素材时必须出现在 `历史素材` 页签；删除 `upload/original` 绑定时素材回到对应素材池。

### 上传测试案例

| ID | 操作 | 预期 UI | 预期文件和数据 |
| --- | --- | --- | --- |
| UP-01 | 在 `上传素材` 页签点击 Upload，选择 2 张图片 | `Images` 数量增加，缩略图正常显示 | 文件进入 `SessionOutput/storyboard/assets/images/`，素材元数据 `asset_type/kind = image` |
| UP-02 | 点击 Upload，选择 2 个视频 | `Videos` 数量增加，视频卡显示视频预览或视频标识 | 文件进入 `SessionOutput/storyboard/assets/videos/`，素材元数据 `asset_type/kind = video` |
| UP-03 | 点击 Upload，选择 2 个声音文件 | `Audios` 数量增加，声音卡显示声音图标或声音标识 | 文件进入 `SessionOutput/storyboard/assets/audios/`，素材元数据 `asset_type/kind = audio` |
| UP-04 | 拖放图片、视频、声音混合文件到上传区域 | 三类分组分别增加，不互相混入 | 三类文件分别进入 images / videos / audios 目录 |
| UP-05 | 上传 `.txt` 或其它无效文件 | UI 显示拒绝或忽略，不出现破图卡片 | 无效文件不写入 assets，也不进入素材元数据 |
| UP-06 | 上传同名图片两次 | 出现两个可区分素材卡，或后一次有批次前缀 | 不覆盖旧上传素材，路径唯一 |
| UP-07 | 上传同名视频两次 | 出现两个可区分素材卡 | 不覆盖旧上传素材，路径唯一 |
| UP-08 | 上传同名声音两次 | 出现两个可区分素材卡 | 不覆盖旧上传素材，路径唯一 |
| UP-09 | 使用 Folder 上传包含子目录的混合素材 | 有效素材全部被收集，仍按类型分组 | 原始相对路径可追踪；无效文件不入库 |

### 拖拽基础测试案例

以下案例每一条都必须执行 `拖拽 -> Save -> 刷新页面 -> 重新打开 Task #31 -> 检查 UI 和 JSON`。

| ID | 来源 | 素材类型 | 目标槽位 | 预期 |
| --- | --- | --- | --- | --- |
| DR-01 | 原视频素材 | 图片 | Dialogue 原图参考 | 只更新 / 保留 `dialogue.image_path` 或 `key_frame_paths[]`，不复制到 `Working/` |
| DR-02 | 原视频素材 | 图片 | Dialogue `Image_01` | `working_assets.images[0].path` 指向 `visual/srt_frames/...`，`source_type = original`，不复制到 `Working/` |
| DR-03 | 原视频素材 | 图片 | Dialogue `Image_02` | `Image_02.path` 指向原参考帧路径，`source_type = original`，`Image_01` 不被覆盖 |
| DR-04 | 上传素材 | 图片 | Dialogue `Image_01` | `Image_01.path` 指向 `assets/images/...`，`source_type = upload`，不复制到 `Working/`，素材卡显示 `已用` |
| DR-05 | 上传素材 | 图片 | Dialogue `Image_02` | `Image_02.path` 指向 `assets/images/...`，`source_type = upload`，独立保存 |
| DR-06 | 上传素材 | 视频 | Dialogue `Video_Final` | `working_assets.video.path` 指向 `assets/videos/...`，`source_type = upload`，不复制到 `Working/`；若该视频成为当前 Final，必须抽取或刷新同 key `TailFrame.png` |
| DR-07 | 上传素材 | 声音 | Dialogue `Audio_Final` | `working_assets.audio.path` 指向 `assets/audios/...`，`source_type = upload`，不复制到 `Working/` |
| DR-08 | 历史素材 | 图片 | Dialogue `Image_01` | 恢复为当前素材时必须复制回 `Working/`，并写为 `source_type = generated` |
| DR-09 | 历史素材 | 视频 | Dialogue `Video_Final` | 恢复为当前素材时必须复制回 `Working/`，并写为 `source_type = generated`；同步抽取或刷新同 key `TailFrame.png` |
| DR-10 | 历史素材 | 声音 | Dialogue `Audio_Final` | 恢复为当前素材时必须复制回 `Working/`，并写为 `source_type = generated` |

### 排列组合测试案例

每组组合都要验证保存前 UI、保存后 UI、刷新后 UI、JSON 字段四个层面。

| ID | 操作 | 预期 |
| --- | --- | --- |
| CB-01 | 同一个 Dialogue 中，`Image_01` 放图片 A，`Image_02` 放图片 B | 两个图片槽都保留，顺序稳定 |
| CB-02 | 同一个 Dialogue 中，同时放 `Image_01`、`Image_02`、`Video_Final`、`Audio_Final` | 四个槽位保存刷新后都存在，互不串写 |
| CB-03 | 同一个 Scene 中，Dialogue 1 放图片 A，Dialogue 2 放图片 B | 两个 Dialogue 保存刷新后互不串写 |
| CB-04 | Dialogue 1 和 Dialogue 2 使用同一个上传图片 | 如果产品允许复用，两处都显示 `已用` 或等价复用状态；保存刷新后两处都有效 |
| CB-05 | Dialogue 1 和 Dialogue 2 使用同一个上传视频 | 如果产品允许复用，两个 `Video_Final` 都保留；如果不允许，第二次拖拽应有明确拒绝 |
| CB-06 | Dialogue 1 和 Dialogue 2 使用同一个上传声音 | 如果产品允许复用，两个 `Audio_Final` 都保留；如果不允许，第二次拖拽应有明确拒绝 |
| CB-07 | 原图参考保留原视频图片，同时把上传图片 / 原视频图片 / 历史图片拖入不同 Dialogue 图片槽 | 原图参考和原视频绑定仍指向 `visual/srt_frames/`；上传绑定指向 `assets/images/`；历史恢复后成为当前有效素材 |
| CB-08 | 一个 Dialogue 已有 `Audio_Final`，再点击生成 TTS | 如果旧音频是 generated，旧 `Working` 文件先进入 history，再写新 TTS；如果旧音频是 upload/original，只解除绑定并写入新 generated 音频 |
| CB-09 | 一个 Dialogue 已有 `Video_Final`，再替换视频 | 旧视频素材卡回到可用状态，新视频卡显示 `已用` |
| CB-10 | 一个 Dialogue 已有 `Image_01` 和 `Image_02`，只替换 `Image_01` | `Image_02` 不变化 |
| CB-11 | 拖拽素材后把 Dialogue 移动到另一个 Scene | 素材路径和 `dialogue_asset_key` 不变，不进入 history |
| CB-12 | 拖拽素材后合并两个 Scene | 两个 Scene 内仍存在的 Dialogue 素材不变，不进入 history |

### 覆盖替换测试案例

| ID | 操作 | 预期 |
| --- | --- | --- |
| RP-01 | Dialogue `Image_01` 从图片 A 替换为图片 B | 只更新该 Dialogue 的 `working_assets.images[0].path`，不影响 `Image_02`；如果 A 是 `generated` 则进入 history，如果 A 是 `upload/original` 则只解除绑定 |
| RP-02 | Dialogue `Image_02` 从图片 A 替换为图片 B | 只更新该 Dialogue 的 `working_assets.images[1].path`；旧素材按 `source_type` 决定进 history 或回素材池 |
| RP-03 | Dialogue `Video_Final` 从视频 A 替换为视频 B | 只更新该 Dialogue 的 `working_assets.video.path`；旧 `generated` 视频进 history，旧 `upload` 视频回上传素材池；旧 TailFrame 失效，新视频抽取新 TailFrame |
| RP-04 | Dialogue `Audio_Final` 从声音 A 替换为声音 B | 只更新该 Dialogue 的 `working_assets.audio.path`；旧 `generated` 声音进 history，旧 `upload` 声音回上传素材池 |
| RP-05 | 替换素材后重排 Shot / Scene | 新素材仍绑定原 Dialogue，不进入 history |
| RP-06 | 替换后立即 Save，再刷新 | 新素材保留；旧 `generated` 出现在 `历史素材` 页签，旧 `upload/original` 回到对应素材池 |
| RP-08 | 重新生成同一槽位且输出文件名相同 | 旧 generated 文件必须先进入 history，再写入同名新文件；不能直接覆盖丢失旧文件 |
| RP-07 | 替换后不 Save，刷新或离开再回来 | 如果产品没有自动保存，应恢复旧素材；如果有离开确认，应提示未保存 |

### 跨类型误拖测试案例

跨类型误拖必须拒绝或保持无变化，不能写入错误字段。

| ID | 操作 | 预期 |
| --- | --- | --- |
| WT-01 | 视频拖到 Dialogue `Image_01` / `Image_02` | 不写入 `working_assets.images[].path`，图片槽不显示视频 |
| WT-02 | 声音拖到 Dialogue `Image_01` / `Image_02` | 不写入 `working_assets.images[].path`，图片槽不显示声音 |
| WT-03 | 图片拖到 Dialogue `Video_Final` | 不写入 `working_assets.video.path` |
| WT-04 | 声音拖到 Dialogue `Video_Final` | 不写入 `working_assets.video.path` |
| WT-05 | 图片拖到 Dialogue `Audio_Final` | 不写入 `working_assets.audio.path` |
| WT-06 | 视频拖到 Dialogue `Audio_Final` | 不写入 `working_assets.audio.path` |
| WT-07 | 无效文件拖到任何槽位 | 不写入任何 StoryBoard 字段 |
| WT-08 | 任意素材拖到页面空白区域 | 不改变当前 StoryBoard |

### 删除、回收站和最终删除测试案例

删除必须区分“从当前槽位移除”、“从上传素材库删除”和“从 history 最终删除”。

#### 从槽位移除到 history

从图片、音频或视频槽位移除时，必须按来源处理。`generated` 素材已经物化到 `Working/`，必须先进入 `assets/history/`；`upload/original` 只是绑定，删除后回到对应素材池。

| ID | 操作 | 预期 |
| --- | --- | --- |
| RM-01 | 移除 Dialogue `Image_01` 的 generated 图片 | 只清空该 Dialogue 的 `working_assets.images[0].path`，原 `Working` 图片进入 history |
| RM-02 | 移除 Dialogue `Image_01` 的 upload/original 图片 | 只清空该 Dialogue 的绑定，素材回到上传素材池或原视频素材池，不进入 history |
| RM-03 | 移除 Dialogue `Video_Final` 的 generated 视频 | 只清空该 Dialogue 的 `working_assets.video.path`，原 `Working` 视频进入 history |
| RM-04 | 移除 Dialogue `Video_Final` 的 upload 视频 | 只清空该 Dialogue 的绑定，上传视频回上传素材池，不进入 history |
| RM-05 | 移除 Dialogue `Audio_Final` 的 generated 音频 | 只清空该 Dialogue 的 `working_assets.audio.path`，原 `Working` 音频进入 history |
| RM-06 | 移除 Dialogue `Audio_Final` 的 upload 音频 | 只清空该 Dialogue 的绑定，上传声音回上传素材池，不进入 history |
| RM-07 | 移除槽位后重排 Scene | 空槽状态仍跟随原 Dialogue |
| RM-08 | 移除后 Save 并刷新 | 被移除槽位仍为空；generated 在 history，可上传/原视频素材回到对应素材池 |
| RM-09 | 移除后不 Save 并刷新 | 如果没有自动保存，应恢复删除前状态；如果有未保存提示，应能阻止误丢 |
| RM-10 | 从 history 把刚移除 generated 素材拖回槽位 | 素材复制回当前 Dialogue 的 `Working/` 路径，并写为 `source_type = generated`；当前槽位不直接引用 history 文件 |

#### 从上传素材库删除

从上传素材库删除只删除上传素材池条目。上传素材绑定没有进入 `Working/`，因此删除绑定或删除上传素材池条目时不进入 history；所有引用该上传素材的槽位必须清空。

| ID | 操作 | 预期 |
| --- | --- | --- |
| DL-01 | 删除未被使用的上传图片 | 图片卡从素材池消失，不影响 StoryBoard |
| DL-02 | 删除已绑定到 Dialogue 图片槽的上传图片 | 上传素材卡消失；所有引用该上传图片的槽位清空，不进入 history |
| DL-03 | 删除已绑定到 `Video_Final` 的上传视频 | 上传素材卡消失；当前视频槽清空，不进入 history |
| DL-04 | 删除已绑定到 `Audio_Final` 的上传声音 | 上传素材卡消失；当前声音槽清空，不进入 history |
| DL-05 | 同一个上传素材被多个 Dialogue 绑定使用后删除 | 所有受影响 Dialogue 引用清空，不能残留坏路径，不进入 history |
| DL-06 | 删除 Dialogue 本身 | 该 Dialogue 的 `generated` 素材进入 history；`upload/original` 绑定只解除，上传素材本体不被误删 |
| DL-07 | 删除后 Save 并刷新 | 被删除上传素材不再出现在上传页签；相关绑定字段为空 |
| DL-08 | 删除原视频素材 | 不应出现删除按钮；如果有删除入口，必须拒绝删除只读原图参考 |

#### 从 history 最终删除

history 是回收站。只有在 history 中执行删除，才可以永久删除历史文件。

| ID | 操作 | 预期 |
| --- | --- | --- |
| HD-01 | 在 history 中删除未被当前 StoryBoard 引用的新图 | history 文件和 manifest 条目被移除，当前 StoryBoard 不受影响 |
| HD-02 | 在 history 中删除未被当前 StoryBoard 引用的视频 | history 文件和 manifest 条目被移除 |
| HD-03 | 在 history 中删除未被当前 StoryBoard 引用的音频 | history 文件和 manifest 条目被移除 |
| HD-04 | 尝试删除已被重新拖回当前槽位的历史素材 | 必须拒绝，或先要求用户从当前槽位移除后再最终删除 |
| HD-05 | history 最终删除后刷新页面 | 被最终删除的素材不再出现在历史素材页签 |

### 保存和刷新测试案例

| ID | 操作 | 预期 |
| --- | --- | --- |
| SV-01 | 任一槽位变化后 Save | Save 按钮进入 saving 状态后恢复，页面无错误 |
| SV-02 | Save 后刷新浏览器 | 所有槽位状态与保存前一致 |
| SV-03 | Save 后重新进入 Task #31 | 所有素材绑定仍存在 |
| SV-04 | Save 后检查 JSON | 只有目标字段变化，没有同 Scene 或其它 Scene 的误写 |
| SV-05 | 多次连续拖拽再 Save | 最终状态以最后一次拖拽为准 |
| SV-06 | 拖拽过程中取消或放到无效区域 | Save 后不产生半写入状态 |
| SV-07 | 拖拽后执行 Split Scene 再 Save | Dialogue 的 `dialogue_asset_key` 不变，素材不串到其它 Dialogue |
| SV-08 | 拖拽后执行 Merge Scene 再 Save | 仍存在 Dialogue 的素材引用保持不变，不生成 history |
| SV-09 | 拖拽后执行 Split Shot 再 Save | 所有受影响 Dialogue 的 `dialogue_asset_key` 不变且不重复 |
| SV-10 | 拖拽后执行 Merge Shot 再 Save | 素材路径不因 Shot 合并而丢失或覆盖 |
| SV-11 | 合并两个 Dialogue 后 Save | 前一个 Dialogue 保留自身素材；后一个消失 Dialogue 的素材进入 history |
| SV-12 | 删除一个 Dialogue 后 Save | 被删 Dialogue 的 `generated` 素材进入 history；`upload/original` 绑定解除；当前 `srt_storyboard.json` 不再引用 |

### 历史素材测试案例

| ID | 操作 | 预期 |
| --- | --- | --- |
| HS-01 | 进入 `历史素材` 页签 | 历史素材按 batch 分组显示 |
| HS-02 | 历史图片拖到 Dialogue `Image_01` | 只更新该 Dialogue |
| HS-03 | 历史图片拖到 `Image_01` / `Image_02` | 只更新对应 Dialogue 图片槽 |
| HS-04 | 历史视频拖到 `Video_Final` | 只更新对应 Dialogue 视频槽 |
| HS-05 | 历史声音拖到 `Audio_Final` | 只更新对应 Dialogue 声音槽 |
| HS-06 | 历史素材被使用后 Save 刷新 | 历史素材路径仍可读，UI 不破图、不丢预览 |
| HS-07 | 使用历史素材后再次重排分组 | 历史素材引用继续跟随原 Dialogue，不进入新的 history |
| HS-08 | 删除绑定历史素材的 Dialogue | 该 Dialogue 引用的 generated/history 素材按删除规则进入新的 history 版本或记录二次归档，不静默消失 |
| HS-09 | 在 history 中点击删除历史素材 | 执行最终删除，移除文件和 manifest 条目 |
| HS-10 | history 最终删除后尝试拖回素材 | 素材不再可见，也不能生成坏引用 |

### 生成和声音槽测试案例

| ID | 操作 | 预期 |
| --- | --- | --- |
| TT-01 | 当前 Dialogue 没有 `Audio_Final` 时点击 Dialogue 播放 | 生成 TTS，写入 `Working/{dialogue_asset_key}_Audio_Final.{ext}` 和该 Dialogue 的 `working_assets.audio.path` |
| TT-02 | 当前 Dialogue 已有上传声音作为 `Audio_Final`，点击播放 | 如果配置一致可播放现有声音；如果需要 TTS 生成，必须明确覆盖或替换 |
| TT-03 | 修改 TTS Provider / Model / Voice 后播放 | `config_key` 变化，必须重新生成 |
| TT-04 | 修改 Tempo 后播放 | `config_key` 变化，必须重新生成 |
| TT-05 | 修改 Dialogue 文本后播放 | `config_key` 变化，必须重新生成，不能朗读旧文本 |
| TT-06 | 播放完成后 Save 刷新 | `Audio_Final` 和实际音频时长回写仍保留 |
| TT-07 | 把 Dialogue 移到另一个 Scene 后播放 | 如果文本和 TTS 设置未变，可复用同 `dialogue_asset_key` 的音频缓存 |
| TT-08 | 点击某条 Dialogue 的 `生成TTS` / 播放按钮 | 浏览器实际 `Audio.src` 指向该 Dialogue 的 `working_assets.audio.path`，文件名使用该 Dialogue 的 `dialogue_asset_key` |
| TT-09 | 连续播放两个 Dialogue | 第二条 Dialogue 播放时 `Audio.src` 必须切换为第二条 Dialogue 的音频路径，不能沿用上一条 Dialogue 或 Scene 级缓存 |
| TT-10 | 底部 Timeline 点击播放一个 Scene | Scene 内每条 Dialogue 逐条播放；每次播放前 `Audio.src` 都与当前 Dialogue 的 `working_assets.audio.path` 一致 |
| TT-11 | 生成音频后 Save 刷新，再点击该 Dialogue 播放 | UI 音频卡、JSON 中的 `working_assets.audio.path`、浏览器 `Audio.src` 三者路径一致 |
| TT-12 | `Audio.src` 中出现非当前 Dialogue 的 `dialogue_asset_key`、Scene ID 或临时 timeline clip ID | 必须判定为失败；不能只因为听到声音就通过 |
| TT-13 | 删除某条 generated `Audio_Final` 后 Save，再点击 Scene / Timeline 播放 | 前端内存缓存和 locked cache 必须先校验当前绑定与文件存在性；旧文件已删除时必须重新生成，不能播放旧 URL 或报 404 |

### Computer Use 证据要求

每个测试案例至少保留以下证据：

- 操作前截图。
- 拖拽或上传后的截图。
- Save 后截图。
- 刷新或重新打开 Task 后截图。
- 对应 JSON 字段片段。
- 对应文件路径存在性检查。
- 如果测试声音，必须保留真实浏览器 `Audio` 播放证据，不能只看按钮变为播放态。
- 如果测试声音，必须记录 `Audio.src`、当前 Dialogue 的 `dialogue_id` / `dialogue_asset_key`、以及 `working_assets.audio.path`，三者必须能一一对应。

通过标准：

- 素材类型不串。
- 素材来源不串。
- Dialogue 素材不扩散到 Scene 公共字段。
- 一个 Dialogue 的素材不扩散到同 Scene 的其它 Dialogue。
- `Image_01`、`Image_02`、`Video_Final`、`Audio_Final` 互不覆盖。
- Shot / Scene 重排不改 `dialogue_asset_key`，不生成 history。
- Dialogue 删除 / 合并导致 Dialogue 消失时，消失 Dialogue 的素材进入 history。
- 替换后旧素材从 `已用` 状态回到可用状态。
- 从槽位移除后，`generated` 素材进入 history；`upload/original` 素材回到对应素材池。
- 从上传素材库删除后所有引用清空，不进入 history。
- 只有在 history 中手动删除，才允许最终删除文件。
- Save、刷新、重新进入 Task 后状态一致。
- `Asset Pool` 的当前页签在拖拽、上传、删除、Save、刷新后保持不变，不能自动跳回 `原视频素材`。
- `原视频素材` 作为只读参考池，不能因为绑定、删除绑定、Save、刷新、重排或合并被清空；Task #31 当前验收数量为 42。
- 播放生成音频时，真实浏览器 `Audio.src` 必须与当前 Dialogue 的 `working_assets.audio.path` 和 `dialogue_asset_key` 一致。
- 无效文件和跨类型误拖不会写入任何有效 StoryBoard 字段。

### 踩坑记录

- 2026-05-30：右侧 `Asset Pool` 页签最初只靠组件内默认值，拖拽素材、刷新页面或右侧栏重挂载后容易回到第一个 `原视频素材`。修复时必须把页签状态提升到编辑器层，同时在 `AssetPanel` 内做 URL/localStorage 兜底。
- 2026-05-30：只在点击页签时记状态不够。拖拽开始会先选中素材并触发 UI 状态变化，所以素材卡 `pointerdown` / `mousedown` 时也要确认当前页签；上传区 `dragenter` / `dragover` 时要确认停留在 `上传素材`。
- 2026-05-30：浏览器模块缓存会让本地改动看起来没有生效。涉及 `KouboStoryBoardModule.jsx`、`AssetPanel.jsx` 这类跨目录 Vite import 时，验证前要更新 import query 版本，并做一次硬刷新后再进行普通刷新回归。
- 2026-05-30：原视频素材池曾出现被清空的问题。根因方向是把“解除原视频素材绑定”误当成“删除素材本体”或依赖前端临时状态组织素材池。修复和测试必须确认原视频素材从 StoryBoard / SRT 参考帧只读重建，Task #31 为 42 个，删除槽位只清空引用，不删除原素材、不复制进 `Working/`、不进入 history。
- 2026-05-30：播放生成音频时曾出现底部播放有声音但 Dialogue 音频槽没有正确绑定的问题。根因方向是播放 `Audio.src` 使用了 Scene / Timeline / 临时播放 ID，而不是当前 Dialogue 的 `dialogue_asset_key` 和 `working_assets.audio.path`。验收时必须同时看 UI 音频卡、JSON 字段和真实浏览器 `Audio.src`，不能只看“能播放”。
- 2026-05-30：删除某条 generated 音频并 Save 后，前端曾继续使用内存中的旧 `audioSrc`，导致播放器请求已删除的 `Working/{dialogue_asset_key}_Audio_Final.wav` 并返回 404。修复时必须在清空音频槽时同步清掉 Dialogue / Scene 播放缓存，并在复用内存缓存或 locked cache 前校验当前 Dialogue 仍绑定该 output 且文件真实存在；文件不存在时必须回退到后端重新生成。

## 04-02 工具职责

04-02 应负责：

- 生成 `SessionOutput/storyboard/srt_storyboard.json`。
- 创建 `SessionOutput/storyboard/assets/images/`。
- 创建 `SessionOutput/storyboard/assets/videos/`。
- 创建 `SessionOutput/storyboard/assets/audios/`。
- 创建 `SessionOutput/storyboard/assets/history/`。
- 创建 `SessionOutput/storyboard/Working/`。
- 在每个 Dialogue item 中写入 `dialogue_asset_key` 和 `working_assets` 规划路径。
- 生成并规范化全局唯一的 `shot_id`、`scene_id`、`dialogue_id`、`dialogue_asset_key`。
- 不创建 `shots/` 目录。
- 不创建 Scene 子目录。
- 不生成最终音频、图片或视频本体。
- 不删除 `Working/` 中已有文件。
- 不删除 `assets/` 中已有上传素材。

最终素材本体由后续 UI 或工具写入 `Working/`。

## 禁止结构

以下结构不应生成：

```text
SessionOutput/storyboard/shots/
SessionOutput/storyboard/scenes/
SessionOutput/storyboard/tts/
SessionOutput/storyboard/ai_generated/
SessionOutput/storyboard/selected/
SessionOutput/storyboard/Working/shot_001/
SessionOutput/storyboard/Working/scene_001/
```

Dialogue 最终素材必须平铺在 `Working/` 目录下。
