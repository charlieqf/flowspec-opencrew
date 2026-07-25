# Koubo StoryBoard 新增 Shot / Scene / Dialogue 素材拖拽绑定回归案例集

版本：v0.1

状态：最小覆盖设计稿。本文用于覆盖新增 Shot / Scene / Dialogue 后，Audio / 原图 / 新图 / 新视频 / 终视频拖拽绑定的回归风险。

## 1. 背景问题

已发现的回归问题有两类：

1. 新增 Dialogue 复制上一条 Dialogue 时，如果继承 `srt_id` / `srt_ids` / `dialogue_asset_key` / `working_assets`，会导致新增 Dialogue 与上一条共用素材锚点。新视频与终视频按 `{dialogue_asset_key}_Video_*` 派生状态和反写绑定，容易一条绑定变成多条绑定。
2. 新增 Dialogue 绑定 Audio 时，Audio 曾经先绑定成功，又被前端“生成音频时长可疑”保护自动清空。短文本或 0 秒 Dialogue 绑定较长 Audio 时尤其容易触发。

本案例集的目标不是穷举所有槽位颜色组合，而是最小覆盖以下关键逻辑：

- 新增 Dialogue 必须生成唯一、独立、稳定的 `dialogue_asset_key`。
- Split Scene / Split Shot 是移动既有 Dialogue，不能破坏既有 Dialogue 的素材 key 和绑定。
- 五类拖拽槽位都必须落到当前目标 Dialogue，不能串到上一条或相邻条。
- 保存、刷新、后端重算后，绑定结果必须仍然保持。
- 手动绑定 Audio 不允许被自动清空。

## 2. 测试基线

统一使用一个最小 StoryBoard：

| 元素 | 初始值 |
| --- | --- |
| Shot | `shot_001` |
| Scene | `scene_001` |
| Dialogue 1 | `dialogue_id=scene_001_dialogue_001`, `srt_id=srt_0001`, `dialogue_asset_key=srt_0001` |
| Dialogue 2 | `dialogue_id=scene_001_dialogue_002`, `srt_id=srt_0002`, `dialogue_asset_key=srt_0002` |

准备 5 个可拖拽素材：

| 素材 | 类型 | 用途 |
| --- | --- | --- |
| `IMG_SOURCE_A` | Image | 原图槽 |
| `IMG_NEW_A` | Image | 新图槽 |
| `AUDIO_A` | Audio | Audio 槽，建议时长大于新增 Dialogue 文本自然时长，用于覆盖误清空问题 |
| `VIDEO_RAW_A` | Video | 新视频槽 |
| `VIDEO_FINAL_A` | Video | 终视频槽 |

标准落盘路径期望：

| 槽位 | 标准路径 |
| --- | --- |
| Audio | `SessionOutput/storyboard/Working/{asset_key}_Audio_Final.*` |
| 原图 | `SessionOutput/storyboard/Working/{asset_key}_Image_Source.*` |
| 新图 | `SessionOutput/storyboard/Working/{asset_key}_Image_New.*` |
| 新视频 | `SessionOutput/storyboard/Working/{asset_key}_Video_Raw.*` |
| 终视频 | `SessionOutput/storyboard/Working/{asset_key}_Video_Final.*` |

## 3. 全局断言

每个案例执行后都必须检查：

| 断言编号 | 断言 |
| --- | --- |
| G-01 | 所有 Dialogue 的 `dialogue_asset_key` 非空且全局唯一。 |
| G-02 | 新增 Dialogue 的 `srt_id=""`，`srt_ids=[]`，不继承相邻 Dialogue 的 `srt_000*`。 |
| G-03 | 新增 Dialogue 的素材路径只能使用自己的 `{dialogue_asset_key}_...` 标准文件名。 |
| G-04 | 绑定目标 Dialogue 后，相邻上一条 / 下一条 Dialogue 的同类槽位不得变化。 |
| G-05 | `koubo_storyboard_edit.json` 与 `srt_storyboard.json` 中的目标绑定一致。 |
| G-06 | 刷新详情接口后，UI / `meta.storyboard_video_slots` / StoryBoard JSON 三者一致。 |
| G-07 | 保存并重新加载后，绑定不丢失、不串绑、不被重算清空。 |

## 4. 最小案例集

### SB-BIND-01 新增 Dialogue 后五类素材绑定

目的：覆盖新增 Dialogue 的 key 生成，以及 Audio / 原图 / 新图 / 新视频 / 终视频五类拖拽绑定。

步骤：

1. 在 `scene_001_dialogue_002` 后点击新增 Dialogue。
2. 将新增 Dialogue 文本设为 `123`，保持 duration 为 `0` 或非常短。
3. 保存并刷新 StoryBoard 详情。
4. 记录新增 Dialogue 的 `dialogue_asset_key`，记为 `K_NEW`。
5. 依次拖拽绑定：
   - `IMG_SOURCE_A` 到原图槽
   - `IMG_NEW_A` 到新图槽
   - `AUDIO_A` 到 Audio 槽
   - `VIDEO_RAW_A` 到新视频槽
   - `VIDEO_FINAL_A` 到终视频槽
6. 每绑定一个槽位后，刷新详情并检查当前槽位。

期望：

| 检查点 | 期望 |
| --- | --- |
| Key | `K_NEW` 不等于 `srt_0002`，形如 `scene_001_dialogue_003_manual...`。 |
| 原图 | 新增 Dialogue 写入 `{K_NEW}_Image_Source.*`，`srt_0002` 不变化。 |
| 新图 | 新增 Dialogue 写入 `{K_NEW}_Image_New.*`，`srt_0002` 不变化。 |
| Audio | 新增 Dialogue 写入 `{K_NEW}_Audio_Final.*`，不会在 metadata load 后自动清空。 |
| 新视频 | 新增 Dialogue 的 raw 状态只指向 `{K_NEW}_Video_Raw.*`。 |
| 终视频 | 新增 Dialogue 的 final 状态只指向 `{K_NEW}_Video_Final.*`。 |
| 相邻项 | `scene_001_dialogue_002` 的 Audio / 原图 / 新图 / 新视频 / 终视频均不被新增 Dialogue 的绑定覆盖。 |

最小自动化断言：

```text
len(unique(dialogue_asset_key)) == dialogue_count
new_dialogue.dialogue_asset_key != previous_dialogue.dialogue_asset_key
new_dialogue.working_assets.audio.path contains "{K_NEW}_Audio_Final"
new_dialogue.source_image_paths[0] contains "{K_NEW}_Image_Source"
new_dialogue.working_assets.images[0].path contains "{K_NEW}_Image_New"
video_slots.by_dialogue_id[new_dialogue_id].raw_video_path contains "{K_NEW}_Video_Raw"
video_slots.by_dialogue_id[new_dialogue_id].final_video_path contains "{K_NEW}_Video_Final"
previous_dialogue paths do not contain "{K_NEW}_"
```

### SB-BIND-02 连续新增两个 Dialogue 后跨条绑定隔离

目的：覆盖连续新增时 manual key 不重复，以及视频按 asset key 反写时不会一带二。

步骤：

1. 在同一 Scene 内连续新增两个 Dialogue，得到 `D_NEW_1` 和 `D_NEW_2`。
2. 分别记录 `K_NEW_1` 和 `K_NEW_2`。
3. 给 `D_NEW_1` 绑定 `VIDEO_FINAL_A`。
4. 给 `D_NEW_2` 绑定另一个终视频素材，或先清空再绑定同一个视频素材。
5. 刷新详情并检查两个 Dialogue。

期望：

| 检查点 | 期望 |
| --- | --- |
| Key | `K_NEW_1 != K_NEW_2`，且都不等于 `srt_0002`。 |
| 终视频 | `D_NEW_1` 指向 `{K_NEW_1}_Video_Final.*`；`D_NEW_2` 指向 `{K_NEW_2}_Video_Final.*`。 |
| 反写 | `confirm-final` 或直接拖拽终视频时，只反写命中的一个 Dialogue。 |
| 清空 | 清空 `D_NEW_2` 终视频不得清掉 `D_NEW_1`。 |

### SB-BIND-03 新增 Dialogue 的 Audio 不被自动清空

目的：专门覆盖短文本 / 0 秒 Dialogue 绑定较长 Audio 后被自动清空的问题。

步骤：

1. 新增一个 Dialogue，文本设为 `123`，duration 为 `0`。
2. 拖拽一个明显较长的 `AUDIO_A` 到 Audio 槽。
3. 等待 Audio 元数据加载完成。
4. 刷新详情。

期望：

| 检查点 | 期望 |
| --- | --- |
| 绑定请求 | 后端 `asset-bind` 返回成功。 |
| 自动清空 | 不出现紧随其后的 `asset-clear`。 |
| 文件 | `{K_NEW}_Audio_Final.*` 存在。 |
| JSON | 新增 Dialogue 的 `working_assets.audio.path` 保持 `{K_NEW}_Audio_Final.*`。 |
| UI | Audio 槽显示已绑定并可播放 / 可加载。 |

### SB-BIND-04 Split Scene 后既有 Dialogue 绑定保持

目的：覆盖新增 Scene 的实际入口。Split Scene 不是创建新 Dialogue，而是移动既有 Dialogue；移动后 key 和绑定必须保持。

步骤：

1. 先给 `scene_001_dialogue_002` 绑定 Audio / 原图 / 新图 / 新视频 / 终视频。
2. 在 `scene_001_dialogue_001` 后执行 Split Scene，使 `scene_001_dialogue_002` 移入新 Scene。
3. 保存并刷新详情。
4. 对移动后的 Dialogue 再拖拽替换一次新图和终视频。

期望：

| 检查点 | 期望 |
| --- | --- |
| Scene | 生成新的 Scene，Scene 自身 `asset_key` 重算。 |
| Dialogue key | 被移动 Dialogue 的 `dialogue_asset_key` 保持原值，不新建、不重复。 |
| 既有绑定 | Split 前五类绑定仍在移动后的 Dialogue 上。 |
| 替换绑定 | 替换新图和终视频只影响移动后的 Dialogue，不影响原 Scene 的 Dialogue。 |
| 保存刷新 | 保存并刷新后绑定仍保持。 |

### SB-BIND-05 Split Shot 后既有 Dialogue 绑定保持

目的：覆盖新增 Shot 的实际入口。Split Shot 是移动后续 Scene / Dialogue 到新 Shot；移动后素材 key 和绑定必须保持。

步骤：

1. 构造至少 2 个 Scene，或在 `scene_001_dialogue_001` 后执行 Split Scene 得到两个 Scene。
2. 给后续 Scene 中的一个 Dialogue 绑定五类素材。
3. 在前一个 Dialogue 后执行 Split Shot，使后续 Scene 移入新 Shot。
4. 保存并刷新详情。
5. 对新 Shot 中目标 Dialogue 重新绑定 Audio 和新视频。

期望：

| 检查点 | 期望 |
| --- | --- |
| Shot | 生成新的 Shot，Shot / Scene 编号重算。 |
| Dialogue key | 新 Shot 中既有 Dialogue 的 `dialogue_asset_key` 保持原值且唯一。 |
| 既有绑定 | Split 前的五类绑定仍在目标 Dialogue 上。 |
| 替换 Audio | 重新绑定 Audio 后写入同一个 Dialogue 的 `{asset_key}_Audio_Final.*`，不被自动清空。 |
| 替换新视频 | 重新绑定新视频后只更新目标 Dialogue 的 `{asset_key}_Video_Raw.*`。 |

### SB-BIND-06 历史坏数据加载兜底

目的：覆盖已有草稿中两个 Dialogue 共用同一个 `dialogue_asset_key` 的修复逻辑。

输入：

```json
[
  {
    "dialogue_id": "scene_001_dialogue_002",
    "srt_id": "srt_0002",
    "srt_ids": ["srt_0002"],
    "dialogue_asset_key": "srt_0002"
  },
  {
    "dialogue_id": "scene_001_dialogue_003",
    "srt_id": "srt_0002",
    "srt_ids": ["srt_0002"],
    "dialogue_asset_key": "srt_0002",
    "working_assets": {
      "audio": {"path": ""},
      "images": [{"path": "SessionOutput/storyboard/Working/srt_0002_Image_New.jpg"}],
      "video": {"path": "SessionOutput/storyboard/Working/srt_0002_Video_Final.mov"}
    }
  }
]
```

步骤：

1. 加载上述坏数据。
2. 执行 StoryBoard 详情加载 / `recalculate`。
3. 保存并刷新。

期望：

| 检查点 | 期望 |
| --- | --- |
| 第一条 | 原 `srt_0002` Dialogue 保持 `dialogue_asset_key=srt_0002`。 |
| 第二条 | 重复项改为独立 manual key，例如 `scene_001_dialogue_003_manual`。 |
| 第二条素材 | 重复项继承来的 Audio / 原图 / 新图 / 新视频 / 终视频槽位清空。 |
| 全局唯一 | 重算后所有 `dialogue_asset_key` 唯一。 |

## 5. 覆盖矩阵

| 风险点 | SB-BIND-01 | SB-BIND-02 | SB-BIND-03 | SB-BIND-04 | SB-BIND-05 | SB-BIND-06 |
| --- | --- | --- | --- | --- | --- | --- |
| 新增 Dialogue 独立 key | 是 | 是 | 是 | - | - | 是 |
| 连续新增 key 唯一 | - | 是 | - | - | - | 是 |
| 历史重复 key 兜底 | - | - | - | - | - | 是 |
| Audio 绑定 | 是 | - | 是 | 是 | 是 | 是 |
| 原图绑定 | 是 | - | - | 是 | 是 | 是 |
| 新图绑定 | 是 | - | - | 是 | 是 | 是 |
| 新视频绑定 | 是 | - | - | 是 | 是 | 是 |
| 终视频绑定 | 是 | 是 | - | 是 | 是 | 是 |
| Split Scene 移动保持 | - | - | - | 是 | - | - |
| Split Shot 移动保持 | - | - | - | - | 是 | - |
| 保存 / 刷新 / 重算 | 是 | 是 | 是 | 是 | 是 | 是 |
| 相邻 Dialogue 不串绑 | 是 | 是 | - | 是 | 是 | 是 |

## 6. 自动化落地建议

建议拆成两层测试：

1. 后端契约测试：直接构造 StoryBoard JSON 和素材文件，调用绑定服务与 `recalculate`，验证 JSON / Working 文件 / `storyboard_video_slots`。
2. 前端交互测试：用 Playwright 或现有 StoryBoard smoke 工具执行点击新增、拖拽素材、刷新页面，验证 UI 槽位路径和后端返回一致。

推荐优先自动化顺序：

| 优先级 | 案例 | 原因 |
| --- | --- | --- |
| P0 | SB-BIND-01 | 单案例覆盖新增 Dialogue + 五类槽位，是主回归链路。 |
| P0 | SB-BIND-03 | 覆盖 Audio 绑定成功后自动清空的独立问题。 |
| P0 | SB-BIND-06 | 覆盖历史坏数据和后端兜底。 |
| P1 | SB-BIND-04 | 覆盖新增 Scene / 移动 Dialogue 后绑定保持。 |
| P1 | SB-BIND-05 | 覆盖新增 Shot / 移动 Scene 后绑定保持。 |
| P2 | SB-BIND-02 | 覆盖连续新增和清空隔离。 |

## 7. 通过标准

本案例集全部通过时，必须满足：

1. 新增 Dialogue 不再继承上一条的 `srt_id` / `dialogue_asset_key` / `working_assets`。
2. 所有绑定都以目标 Dialogue 的 `dialogue_asset_key` 生成标准 Working 文件。
3. Audio 手动绑定不会被前端自动清空。
4. 新视频 / 终视频不会因为重复 key 绑定到相邻 Dialogue。
5. Split Scene / Split Shot 只移动既有 Dialogue，不改变既有素材归属。
6. 保存、刷新、重算后，StoryBoard JSON、Working 文件、UI 状态三者一致。
