# Koubo StoryBoard Plan 执行与页面绑定一致性审计

版本：v0.1

状态：只读交叉校验记录。本文基于当前代码与 Task #8 / Session #9 真实产物，检查 Audio / Image / Video / Prompt 在 Plan 生成、执行落盘、后端状态、页面绑定之间是否使用同一套 key。

## 1. 结论

当前仍存在不一致，根因集中在两点：

1. 新增 Dialogue 的页面绑定使用 `dialogue_asset_key`，例如 `scene_001_dialogue_003_manual`。
2. Plan 生成与执行链路仍大量使用 `srt_id or dialogue_id`，例如 `scene_001_dialogue_003`。

因此同一条新增 Dialogue 会出现：

| 类型 | 当前前缀示例 |
| --- | --- |
| 页面拖拽 Audio / 原图 / 新图 | `scene_001_dialogue_003_manual_*` |
| ImagePrompt / VideoPrompt | `scene_001_dialogue_003_*` |
| Video Raw / SegmentAudio / TailFrame | `scene_001_dialogue_004_*` |
| StoryBoard `dialogue_asset_key` | `scene_001_dialogue_004_manual` |

这说明问题不是单个槽位，而是 Plan 关系字段、执行器绑定字段、页面状态字段没有统一。

## 2. 标准口径

所有运行时文件名前缀必须来自：

```text
dialogue.dialogue_asset_key
segment.asset_key = segment.dialogue_ids[0]
```

其中 `segment.dialogue_ids` 的值也必须是 `dialogue_asset_key`。

标准文件名：

| 类型 | 文件名 |
| --- | --- |
| Dialogue Audio | `{dialogue_asset_key}_Audio_Final.*` |
| 原图 | `{dialogue_asset_key}_Image_Source.*` |
| 新图 | `{dialogue_asset_key}_Image_New.*` |
| Image Prompt | `{asset_key}_ImagePrompt.json` |
| Video Prompt | `{asset_key}_VideoPrompt.json` |
| Segment Audio | `{asset_key}_SegmentAudio_Final.*` |
| Raw Video | `{asset_key}_Video_Raw.*` |
| Final Video | `{asset_key}_Video_Final.*` |
| TailFrame | `{asset_key}_TailFrame.*` |

## 3. 代码交叉校验

### 3.1 页面手动绑定

| 文件 | 结论 |
| --- | --- |
| `asset_reference_services.py` | 手动绑定 Audio / 原图 / 新图 / Raw Video / Final Video 时，使用 `dialogue_asset_key(dialogue)` 生成标准文件名。当前逻辑相对一致。 |
| `asset_history_services.py` | 历史素材恢复绑定同样使用 `dialogue_asset_key(dialogue)`。当前逻辑相对一致。 |
| `DialogueCard.jsx` | 页面展示 Audio / 原图 / 新图直接读 StoryBoard 字段；视频状态额外读 `storyboard_video_slots`。 |

页面手动绑定这条线的问题不是文件名生成，而是新增 Dialogue 的 key 仍然是 `manual` 临时 key。

### 3.2 页面状态接口

| 文件 | 不一致点 |
| --- | --- |
| `video_plan_load_services.py` | `storyboard_video_slot_states()` 对 Audio / 原图 / 新图 / ImagePrompt / VideoPrompt / Raw / Final 的存在判断使用 `dialogue_asset_key + dialogue_id + srt_id + srt_ids` 多 key 查找。 |
| `KouboStoryBoardModule.jsx` | `videoSlotState(dialogueId, assetKey)` 优先 `by_dialogue_id`，再 `by_asset_key`。统一 key 后应优先 asset key 或只暴露 asset key。 |

影响：即使文件名前缀已经错了，页面状态也可能通过 fallback 显示成“存在”，掩盖问题。

### 3.3 Video Plan 生成

| 文件 | 不一致点 |
| --- | --- |
| `05_01_VideoPlanGenerator.py` | `dialogue_key(dialogue, index)` 使用 `srt_id or dialogue_id`，没有使用 `dialogue_asset_key`。 |
| `05_01_VideoPlanGenerator.py` | `segment.asset_key` 来自 `dialogue_key()`，新增 Dialogue 会变成 `scene_001_dialogue_003`，不是 `scene_001_dialogue_003_manual` 或未来的层级 key。 |
| `05_01_VideoPlanGenerator.py` | `segment.dialogue_ids` 写入 `srt_id or dialogue_id`，不是 `dialogue_asset_key`。 |
| `05_01_VideoPlanGenerator.py` | `dialogue_audio_tasks[].srt_id` 继续作为执行器索引字段；`planned_audio_path` 虽可能取到 `working_assets.audio.path`，但关系字段还是旧 key。 |

影响：Video Plan 的 prompt、video、segment audio 文件名前缀会和页面绑定的 Audio / Image 前缀分裂。

### 3.4 Video Plan 执行

| 文件 | 不一致点 |
| --- | --- |
| `05_02_VideoPlanExecutor.py` | `flatten_dialogues()` 按 `srt_id or dialogue_id` 建索引。 |
| `05_02_VideoPlanExecutor.py` | `dialogue_match_keys()` 同时接受 `srt_id`、`dialogue_id`、`dialogue_asset_key`、`srt_ids`。 |
| `05_02_VideoPlanExecutor.py` | `bind_segment_output_to_storyboard()` 取 `segment.dialogue_ids[0]` 作为回绑目标。当前这个值不是 `dialogue_asset_key`。 |
| `05_02_VideoPlanExecutor.py` | `sync_generated_outputs_to_edit()` 用多 key 同步 edit StoryBoard。 |

影响：执行生成文件名可以按 Plan 的 `asset_key` 落盘，但回绑目标依赖旧 `dialogue_ids`，容易和统一 key 模型冲突。

### 3.5 Image Plan 生成与执行

| 文件 | 不一致点 |
| --- | --- |
| `05_03_ImagePlanGenerator.py` | `asset_key = first_dialogue_id(segment) or segment.asset_key or segment.segment_id`。由于 Video Plan 的 `dialogue_ids` 是旧 key，Image Plan 继承旧 key。 |
| `05_04_ImagePlanExecutor.py` | `prompt_path_for()` / `image_path_for()` 按 Image Plan 的 `asset_key` 生成 `{asset_key}_ImagePrompt.json` / `{asset_key}_Image_New.png`。 |
| `05_04_ImagePlanExecutor.py` | 生成后调用 `VPE.bind_segment_output_to_storyboard()`，继续依赖 `segment.dialogue_ids[0]`。 |

影响：Image Plan 本身文件名自洽，但它继承了错误的 segment key，不一定和页面 Dialogue key 一致。

### 3.6 Video Only Plan 生成与执行

| 文件 | 不一致点 |
| --- | --- |
| `05_05_VideoOnlyPlanGenerator.py` | `asset_key = first_dialogue_id(segment) or segment.asset_key or segment.segment_id`，继承旧 `dialogue_ids`。 |
| `05_05_VideoOnlyPlanGenerator.py` | `actual_dialogues_by_asset()` 同时用 `dialogue_key`、`asset_key`、`dialogue_asset_key`、`srt_id`、`dialogue_id` 建索引。 |
| `05_06_VideoOnlyPlanExecutor.py` | `prepare_segment_audio()` 继续用 `audio_task.srt_id` 查 Dialogue。 |
| `05_06_VideoOnlyPlanExecutor.py` | `bind_first_frame_to_storyboard()` 先用 `segment.dialogue_ids[0]`，失败后再用多 key fallback。 |
| `video_only_plan_routes.py` | artifact status、confirm-final、materialize-tail-frame 都有 `asset_keys = [actual_asset_key, asset_key]` 兼容查找。 |

影响：Video Only 的 prompt/raw/final/tail 文件名按任务 `asset_key`，但 first frame / audio / final 回绑仍可能靠 fallback 命中。

### 3.7 TTS / Audio

| 文件 | 不一致点 |
| --- | --- |
| `kouboStoryboardTts.js` | 前端 TTS 输出路径使用 `dialogueAssetKey(dialogue)`，但该函数 fallback 到 `srt_id/srt_ids/dialogue_id`。 |
| `tts_workflow_services.py` | 后端 TTS 绑定使用 `dialogue_asset_key`。 |
| `05_01_VideoPlanGenerator.py` | Segment 内 `dialogue_audio_tasks[].planned_audio_path` 使用 `dialogue_key()` 或既有 audio path。若新增 Dialogue 已手动绑定 Audio，则路径可能是 manual key，但 `srt_id` 字段仍是 dialogue_id。 |
| `05_02_VideoPlanExecutor.py` / `05_06_VideoOnlyPlanExecutor.py` | 生成 TTS 时按 `audio_task.srt_id` 查 Dialogue，统一 key 后应改为 `dialogue_asset_key`。 |

影响：页面手动 Audio 绑定可正确落到当前 `dialogue_asset_key`，但 Plan 内 Audio 关系字段仍混乱。

## 4. Task #8 / Session #9 真实产物校验

校验目录：

```text
/Users/duheng/.opencrew/sessions/9/workspace/SessionOutput/storyboard
```

### 4.1 StoryBoard 当前 Dialogue key

| Dialogue | `dialogue_asset_key` | `srt_id` |
| --- | --- | --- |
| `scene_001_dialogue_001` | `srt_0001` | `srt_0001` |
| `scene_001_dialogue_002` | `srt_0002` | `srt_0002` |
| `scene_001_dialogue_003` | `scene_001_dialogue_003_manual` | 空 |
| `scene_001_dialogue_004` | `scene_001_dialogue_004_manual` | 空 |
| `scene_001_dialogue_005` | `scene_001_dialogue_005_manual` | 空 |

页面 StoryBoard 自身没有重复 key，但新增 Dialogue 已经进入 manual key 分支。

### 4.2 Working 文件前缀分裂

| 前缀 | 文件类型 |
| --- | --- |
| `scene_001_dialogue_003` | `ImagePrompt`, `VideoPrompt` |
| `scene_001_dialogue_003_manual` | `Audio_Final`, `Image_New`, `Image_Source` |
| `scene_001_dialogue_004` | `SegmentAudio_Final`, `TailFrame`, `VideoPrompt`, `Video_Raw` |
| `scene_001_dialogue_004_manual` | `Audio_Final`, `Image_New`, `Image_Source` |
| `scene_001_dialogue_005` | `ImagePrompt`, `VideoPrompt` |
| `scene_001_dialogue_005_manual` | `Audio_Final`, `Image_New`, `Image_Source` |

结论：Prompt / Video / SegmentAudio 走旧 `dialogue_id`；页面素材绑定走 `manual dialogue_asset_key`。

### 4.3 Video Plan 不一致

| Segment | `asset_key` | `dialogue_ids` | 不一致 |
| --- | --- | --- | --- |
| `shot_001_scene_001_segment_002` | `scene_001_dialogue_003` | `["scene_001_dialogue_003"]` | `image_path` 实际为 `scene_001_dialogue_003_manual_Image_New.png` |
| `shot_001_scene_001_segment_003` | `scene_001_dialogue_004` | `["scene_001_dialogue_004"]` | `image_path` 实际为 `scene_001_dialogue_004_manual_Image_New.png` |
| `shot_001_scene_001_segment_004` | `scene_001_dialogue_005` | `["scene_001_dialogue_005"]` | `image_path` 实际为 `scene_001_dialogue_005_manual_Image_New.png` |

Audio task 也体现了分裂：

| Segment asset | `audio_task.srt_id` | `planned_audio_path` 前缀 |
| --- | --- | --- |
| `scene_001_dialogue_003` | `scene_001_dialogue_003` | `scene_001_dialogue_003_manual` |
| `scene_001_dialogue_004` | `scene_001_dialogue_004` | `scene_001_dialogue_004_manual` |
| `scene_001_dialogue_005` | `scene_001_dialogue_005` | `scene_001_dialogue_005_manual` |

### 4.4 Image Plan 不一致

Image Plan 的任务本身是自洽的：

| Task | `asset_key` | `dialogue_ids` |
| --- | --- | --- |
| `shot_001_scene_001_scene_001_dialogue_003_image` | `scene_001_dialogue_003` | `["scene_001_dialogue_003"]` |
| `shot_001_scene_001_scene_001_dialogue_004_image` | `scene_001_dialogue_004` | `["scene_001_dialogue_004"]` |
| `shot_001_scene_001_scene_001_dialogue_005_image` | `scene_001_dialogue_005` | `["scene_001_dialogue_005"]` |

但它继承的是 Video Plan 的旧 key，不是 StoryBoard 的 `dialogue_asset_key`，所以与页面素材绑定不一致。

### 4.5 Video Only Plan 不一致

| Task | `asset_key` | 不一致 |
| --- | --- | --- |
| `shot_001_scene_001_scene_001_dialogue_003_video_only` | `scene_001_dialogue_003` | `first_frame_path` 前缀是 `scene_001_dialogue_003_manual` |
| `shot_001_scene_001_scene_001_dialogue_004_video_only` | `scene_001_dialogue_004` | 当前 raw/video 走 `scene_001_dialogue_004`，页面图片/audio 走 manual |
| `shot_001_scene_001_scene_001_dialogue_005_video_only` | `scene_001_dialogue_005` | `first_frame_path` 前缀是 `scene_001_dialogue_005_manual` |

## 5. 需要统一的修改点

优先级从根到叶：

1. `05_01_VideoPlanGenerator.dialogue_key()` 改为优先且正常只使用 `dialogue_asset_key`。
2. `05_01_VideoPlanGenerator.segment.dialogue_ids` 改为写 `dialogue_asset_key`。
3. `dialogue_audio_tasks` 增加或改用 `dialogue_asset_key` 字段；执行器不能再用 `srt_id` 查 Dialogue。
4. `05_02_VideoPlanExecutor.flatten_dialogues()` 改为按 `dialogue_asset_key` 建索引。
5. `05_02_VideoPlanExecutor.bind_segment_output_to_storyboard()` 改为用 `segment.dialogue_ids[0]` 的 asset key 精确回绑。
6. `05_03_ImagePlanGenerator` / `05_05_VideoOnlyPlanGenerator` 只接受 Video Plan 中的 asset key，不再 fallback 到 `segment_id`。
7. `05_04_ImagePlanExecutor` / `05_06_VideoOnlyPlanExecutor` 继续使用任务 `asset_key` 生成文件，但回绑必须精确按 `dialogue_asset_key`。
8. `image_plan_routes.py` / `video_plan_artifact_services.py` / `video_plan_load_services.py` / `video_only_plan_routes.py` 移除运行时多 key fallback。
9. `KouboStoryBoardModule.jsx` 的 `videoSlotState()` 应优先或仅按 asset key 取状态。
10. 迁移现有 `*_manual` 文件到层级 key 后，再重新生成三类 Plan。

## 6. 验收检查清单

每次生成或绑定后必须检查：

| 检查项 | 验收标准 |
| --- | --- |
| Audio | `working_assets.audio.path` 前缀等于 `dialogue_asset_key` |
| 原图 | `source_image_paths[0]` / `image_path` 前缀等于 `dialogue_asset_key` |
| 新图 | `working_assets.images[0].path` / `bound_image_path` 前缀等于 `dialogue_asset_key` |
| ImagePrompt | `planned_outputs.image_prompt_path` 前缀等于 `segment.asset_key` |
| VideoPrompt | `planned_outputs.video_prompt_path` 前缀等于 `segment.asset_key` |
| SegmentAudio | `planned_outputs.segment_audio_path` 前缀等于 `segment.asset_key` |
| Raw Video | `planned_outputs.raw_video_path` 前缀等于 `segment.asset_key` |
| Final Video | `planned_outputs.final_video_path` / StoryBoard video path 前缀等于 `segment.asset_key` |
| TailFrame | `tail_frame.planned_path` / `tail_frame_path` 前缀等于 `segment.asset_key` |
| Segment relation | `segment.dialogue_ids` 每一项都能在当前 StoryBoard 的 `dialogue_asset_key` 集合中精确找到 |
| 页面状态 | 不通过 `srt_id` / `dialogue_id` / index suffix fallback 显示存在 |
