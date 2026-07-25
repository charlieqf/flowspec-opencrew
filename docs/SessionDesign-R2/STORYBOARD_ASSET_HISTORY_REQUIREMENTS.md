# StoryBoard Asset History Requirements

## 背景

`SessionOutput/storyboard/Working/` 是当前 StoryBoard 中新生成素材的最终区。口播 StoryBoard 的素材身份跟随 Dialogue / `srt_id`，不跟随 Shot 或 Scene。

Working 文件名必须依赖稳定的 `dialogue_asset_key`，而不是当前 Scene 的 `asset_key`：

```text
SessionOutput/storyboard/Working/{dialogue_asset_key}_Audio_Final.wav
SessionOutput/storyboard/Working/{dialogue_asset_key}_Image_01.png
SessionOutput/storyboard/Working/{dialogue_asset_key}_Image_02.png
SessionOutput/storyboard/Working/{dialogue_asset_key}_Video_Final.mp4
```

当用户在 StoryBoard 页面内重新调整 Shot / Scene 分组时，只要 Dialogue 仍然存在，该 Dialogue 的素材路径必须保持有效。Split / Merge Shot、Split / Merge Scene、调整 Scene 顺序或 Shot 归属，都不应备份、清空或重命名 `Working/` 中属于现存 Dialogue 的素材。

Analysis V1 `04_02_StoryBoard` 全量分组是例外：它不是页面内的局部重排，而是从 `rewritten_srt_items.json` 重新生成权威 StoryBoard source。`04_02` 成功后，旧 `Working/` 产物与旧 VideoPlan / ImagePlan / VideoOnlyPlan 状态都必须视为过期。

`assets/history/` 只处理“已经物化到 Working 的 generated 素材从当前 Dialogue 槽位移除”的情况。它不是 Shot / Scene 重分组快照目录。

## 目标

- 保持 `Working/` 只保存当前 Dialogue 的 generated 最终素材。
- Shot / Scene 结构变化时保留现存 Dialogue 的素材路径。
- 让被删除或被替换的 generated 素材进入历史素材池，避免直接永久删除。
- 让历史素材可以作为可复用素材池重新选择，但不能自动冒充当前最终素材。
- 让 `srt_storyboard.json` 继续作为当前结构和当前最终素材路径的唯一索引。

## 目录结构

StoryBoard 素材目录采用三类：

```text
SessionOutput/storyboard/
  srt_storyboard.json

  assets/
    images/
    videos/
    audios/
    history/

  Working/
```

### `assets/images/`

备用图片素材池。

规则：

- 用户上传但尚未绑定到某个 Dialogue 图片槽位的图片放在这里。
- 历史素材中被用户重新提取为备用图片时，也可以复制到这里。
- 这里的素材不是 generated 最终素材。
- 用户将图片绑定到某个 Dialogue 槽位时，只写回该 Dialogue 的 `working_assets.images[].path` 和 `source_type = "upload"`，不复制到 `Working/`。

### `assets/videos/`

备用视频素材池。

规则：

- 用户上传但尚未绑定到某个 Dialogue 视频槽位的视频放在这里。
- 历史素材中被用户重新提取为备用视频时，也可以复制到这里。
- 这里的素材不是 generated 最终素材。
- 用户将视频绑定到某个 Dialogue 槽位时，只写回该 Dialogue 的 `working_assets.video.path` 和 `source_type = "upload"`，不复制到 `Working/`。

### `assets/history/`

generated 素材历史池。

规则：

- 只有当前 Dialogue 槽位中的 generated 素材被删除、替换、或其 Dialogue 被删除 / 合并消失时，才将对应 `Working/` 文件移入新的 history 批次目录。
- 只移动被移除槽位对应的文件，不移动整个 `Working/`。
- Split / Merge Shot、Split / Merge Scene、调整 Scene 顺序或 Shot 归属，不触发 history 备份，不清空 `Working/`。
- Analysis V1 `04_02_StoryBoard` 全量分组成功后，必须将整个 `SessionOutput/storyboard/Working/` 中的旧文件移动到 history 批次目录。这是权威 source 重建，不属于普通 Split / Merge。
- history 中的素材不能直接写入当前 `working_assets.*.path`。
- 如需复用 history 素材，必须先复制回 `Working/`，按目标 Dialogue 的 `dialogue_asset_key` 重新命名，再写回 `srt_storyboard.json`。

推荐目录：

```text
SessionOutput/storyboard/assets/history/
  batch_20260530_153012_remove_slot_asset/
    manifest.json
    srt_0001_02_Audio_Final.wav
    srt_0001_02_Image_01.png
    srt_0001_02_Image_02.png
    srt_0001_02_Video_Final.mp4

  batch_20260613_180000_04_02_full_grouping_reset_working/
    manifest.json
    Working/
      srt_0001_Image_01.png
      srt_0001_Video_Final.mp4
```

## History 触发条件

以下操作会移除当前 Dialogue 槽位里的 generated 素材，必须将对应 `Working/` 文件移入 `assets/history/`：

- 删除某个 Dialogue 的 generated 图片、音频或视频槽位。
- 用新的 generated 素材替换某个 Dialogue 已有 generated 素材。
- 删除 Dialogue，且该 Dialogue 有 generated 素材在 `Working/` 中。
- Merge Dialogue 时，后一个 Dialogue 被并入前一个 Dialogue 并消失，且被移除 Dialogue 有 generated 素材在 `Working/` 中。
- 用户确认清理某个 orphan generated 文件。
- Analysis V1 `04_02_StoryBoard` 全量分组成功，旧 StoryBoard source 被新 source 替换。

以下操作不应触发 `Working/` 备份或清空：

- Split Scene。
- Split Shot。
- Merge Scene。
- Merge Shot。
- Fixed Timing / 自动重分组，只要没有删除 Dialogue 或移除 generated 槽位。
- 任何只改变 Scene 边界、Scene 顺序、Scene 所属 Shot、Shot / Scene 编号的操作。
- 修改对白文字。
- 修改单条 Dialogue 时长，但不改变 Scene 边界。
- 更换 TTS Provider / Model / Voice。
- 重新生成当前 Dialogue 的 TTS 时，如果此前没有有效 generated 音频引用。
- 上传备用图片或视频。
- 将原视频素材或上传素材绑定到当前 Dialogue 槽位。

注意：上述“不触发”列表只适用于页面内编辑和 Quick / Fixed Timing 这类保留当前 Dialogue 语义的重排。`04_02_StoryBoard` 全量分组成功属于权威 source 重建，必须触发 Working 全量归档和下游生成状态重置。

## 保存流程

当用户保存一次 StoryBoard 结构变化时：

1. 保存新的 Shot / Scene / Dialogue 编排结构。
2. 保留所有仍存在 Dialogue 的 `dialogue_asset_key` 和 `working_assets` 路径。
3. 不创建 `regroup_{timestamp}` 目录。
4. 不清空 `SessionOutput/storyboard/Working/`。
5. 不清空现存 Dialogue 的 `working_assets.*.path`。

当用户删除或替换某个 generated 槽位时：

1. 只处理被移除槽位对应的 `Working/` 文件。
2. 创建 history 批次目录 `batch_{timestamp}_{reason}/`。
3. 将被移除文件移动到该批次目录。
4. 写入 history manifest。
5. 清空或更新该 Dialogue 的对应 `working_assets` 槽位。
6. 保存新的 `srt_storyboard.json` 或当前编辑结构。

当 Analysis V1 `04_02_StoryBoard` 全量分组成功时：

1. 将旧 `SessionOutput/storyboard/koubo_storyboard_edit.json` 移入 `assets/history/batch_{timestamp}_analysis_v1_storyboard_source_refreshed/`。
2. 将旧 `SessionOutput/storyboard/Working/` 中的所有文件移动到 `assets/history/batch_{timestamp}_04_02_full_grouping_reset_working/Working/`。
3. 写入 history manifest，`reason = "04_02_full_grouping_reset_working"`，记录每个文件的 `original_path` 和 `history_path`。
4. 保留 `SessionOutput/visual/srt_frames/`、上传素材池、`SessionContext/Consistency/` 和既有 history。
5. 移除旧的 `video_generation_plan.json`、`image_generation_plan.json`、`video_only_generation_plan.json` 以及它们的 execution state/result、UI cache、工具运行目录。
6. 移除依赖旧 VideoPlan / Working 的 compose result/state，使页面回到“尚未生成计划和最终合成”的初始状态。
7. 新 StoryBoard source 只能引用 04_02 新输出中的原始帧和文本结构，不能从旧 Working 残留恢复“新图/视频/音频已完成”状态。

## History Manifest

每个 history 批次必须包含 `manifest.json`，用于后续展示、追溯和复用。

推荐结构：

```json
{
  "schema_version": "storyboard_asset_history_0.1",
  "batch_id": "batch_20260530_153012_remove_slot_asset",
  "reason": "remove_slot_asset",
  "created_at": "2026-05-30T15:30:12+08:00",
  "source_working_path": "SessionOutput/storyboard/Working",
  "history_path": "SessionOutput/storyboard/assets/history/batch_20260530_153012_remove_slot_asset",
  "source_storyboard_path": "SessionOutput/storyboard/srt_storyboard.json",
  "items": [
    {
      "original_path": "SessionOutput/storyboard/Working/srt_0001_02_Audio_Final.wav",
      "history_path": "SessionOutput/storyboard/assets/history/batch_20260530_153012_remove_slot_asset/srt_0001_02_Audio_Final.wav",
      "asset_type": "Audio",
      "slot": "Audio_Final",
      "dialogue_asset_key": "srt_0001_02",
      "dialogue_id": "dialogue_srt_0001_02",
      "srt_id": "srt_0001_02",
      "shot_id": "shot_001",
      "scene_id": "scene_001",
      "reason": "remove_slot_asset"
    }
  ]
}
```

## History 素材复用规则

history 素材只能作为可复用素材来源，不是当前最终素材。

当用户从 History 中选择一个旧素材复用到当前 Dialogue 时：

1. 系统读取目标 Dialogue 的 `dialogue_asset_key`。
2. 根据目标槽位生成新的 Working 文件名。
3. 将 history 文件复制到 `Working/`。
4. 将复制后的路径写入目标 Dialogue 的 `working_assets`。

示例：

```text
History 原文件：
SessionOutput/storyboard/assets/history/batch_20260530_153012_remove_slot_asset/srt_0001_02_Audio_Final.wav

目标 Dialogue：
dialogue_asset_key = srt_0002_04

复制后：
SessionOutput/storyboard/Working/srt_0002_04_Audio_Final.wav
```

并写回：

```json
{
  "working_assets": {
    "audio": {
      "slot": "Audio_Final",
      "source_type": "generated",
      "path": "SessionOutput/storyboard/Working/srt_0002_04_Audio_Final.wav"
    }
  }
}
```

## Asset Pool UI 要求

右侧 Asset Pool 必须提供三个 Tab，用于区分不同来源和不同职责的素材：

```text
Asset Pool
  原视频素材
  上传素材
  历史素材
```

### 原视频素材 Tab

原始 SRT 抽帧参考素材。

来源：

```text
SessionOutput/visual/srt_frames/
```

以及索引链路：

```text
SessionOutput/storyboard/srt_storyboard.json
  -> shots[].scenes[].dialogue_items[].image_path
  -> shots[].scenes[].key_frame_paths[]
```

规则：

- 原视频素材是原视频抽帧参考，不属于 `SessionOutput/storyboard/assets/`。
- 原视频素材不是 generated 最终素材。
- 用户可以预览原视频素材。
- 用户将某个原视频素材绑定到当前 Dialogue 图片槽位时，系统只写入该 Dialogue 的 `working_assets.images[].path` 和 `source_type = "original"`。
- `working_assets.images[].path` 允许直接指向 `SessionOutput/visual/srt_frames/...`。

### 上传素材 Tab

用户上传或从历史中提取的备用图片、视频素材。

来源：

```text
SessionOutput/storyboard/assets/images/
SessionOutput/storyboard/assets/videos/
```

规则：

- 上传素材 Tab 内部必须区分 Images 和 Videos 两类。
- 用户上传图片后进入 `SessionOutput/storyboard/assets/images/`。
- 用户上传视频后进入 `SessionOutput/storyboard/assets/videos/`。
- History 中的旧图片如果被用户提取为备用图片，也可以复制到此目录。
- History 中的旧视频如果被用户提取为备用视频，也可以复制到此目录。
- 用户将图片绑定到当前 Dialogue 图片槽位时，系统写入该 Dialogue 的 `working_assets.images[].path` 和 `source_type = "upload"`，不复制到 `Working/`。
- 用户将视频绑定到当前 Dialogue 视频槽位时，系统写入该 Dialogue 的 `working_assets.video.path` 和 `source_type = "upload"`，不复制到 `Working/`。
- `working_assets.images[].path` 允许指向 `assets/images/...`。
- `working_assets.video.path` 允许指向 `assets/videos/...`。

### 历史素材 Tab

从当前 Dialogue 槽位移除的旧 generated 素材。

来源：

```text
SessionOutput/storyboard/assets/history/
```

历史素材 UI 应展示：

- history 批次名，例如 `batch_20260530_153012_remove_slot_asset`。
- 批次创建时间。
- 批次原因，例如 `remove_slot_asset`、`replace_generated_asset`、`delete_dialogue`、`merge_dialogue_removed`。
- 批次内的音频、图片、视频素材。
- 每个素材的 `dialogue_asset_key`、`dialogue_id`、`srt_id`、槽位类型，以及移除时所在的 `shot_id` / `scene_id`。

历史素材操作：

- 预览。
- 复制到当前 Dialogue 的目标槽位。
- 复制后必须进入 `Working/`，不能直接引用 history 路径。

### Tab 与最终素材路径的关系

三个 Tab 都是素材来源池。它们进入当前 Dialogue 槽位后的路径规则不同：

- 原视频素材：保留原路径，写入 `source_type = "original"`，不复制到 `Working/`。
- 上传素材：保留上传素材池路径，写入 `source_type = "upload"`，不复制到 `Working/`。
- 历史 generated 素材：复制回 `Working/`，按目标 Dialogue 的 `dialogue_asset_key` 重命名，写入 `source_type = "generated"`。
- 新生成素材：写入 `Working/`，按当前 Dialogue 的 `dialogue_asset_key` 命名，写入 `source_type = "generated"`。

因此，当前素材路径可以来自：

```text
SessionOutput/visual/srt_frames/
SessionOutput/storyboard/assets/images/
SessionOutput/storyboard/assets/videos/
SessionOutput/storyboard/assets/audios/
SessionOutput/storyboard/Working/
```

不能让当前槽位直接引用：

```text
SessionOutput/storyboard/assets/history/
```

## 页面刷新策略

StoryBoard 页面采用：

```text
无后台自动刷新 + 用户动作驱动刷新 + 手动全量刷新
```

### 禁止后台自动刷新

页面不允许使用定时轮询或后台静默刷新覆盖当前编辑状态。

规则：

- 不允许定时重新读取 `srt_storyboard.json`。
- 不允许定时重新扫描 `assets/` 或 `Working/`。
- 不允许后台静默刷新 TTS 候选或 locked TTS manifest。
- 不允许在用户有未保存编辑时自动重载并覆盖本地状态。

### 首次加载

用户首次进入某个 StoryBoard Task Detail 页面时，系统自动加载一次当前数据。

首次加载范围：

- StoryBoard 结构。
- 当前 `working_assets`。
- Asset Pool 三个 Tab 的摘要数据。
- 已保存的 TTS 选择或 TTS 候选摘要。

如果同一个 Task 已经加载，并且用户有未保存编辑，路由或页面状态变化不应触发重复全量加载。

### 用户动作驱动的局部刷新

用户执行以下编辑动作后，界面必须立即刷新相关区域，但可以先只更新前端本地状态，不一定全量重拉后端：

- Delete Dialogue。
- Add Dialogue。
- Split Scene。
- Split Shot。
- Merge Scene。
- Merge Shot。
- 修改 Dialogue 时长。
- 更新 Shot / Scene / Dialogue 文本。
- 绑定原视频素材、上传素材或历史素材到当前 Dialogue。
- 切换 TTS Provider / Model / Voice / Tempo / Prompt。

局部刷新范围：

- 左侧 Shot / Scene 树。
- 中央编辑区。
- 底部 Timeline。
- 右侧 Asset Pool 使用状态。
- 当前 dirty 状态。

这些动作完成后必须标记当前页面存在未保存编辑，除非动作本身已经由后端保存并返回权威结果。

### 文件产出类动作后的权威刷新

以下动作会改变真实文件或后端索引，动作完成后必须以后端返回结果为准刷新界面：

- 上传图片或视频。
- 删除上传素材。
- 绑定原视频素材到 Dialogue 槽位。
- 绑定上传素材到 Dialogue 槽位。
- 从 History 复制素材到 `Working/` 并绑定到 Dialogue 槽位。
- 生成 Dialogue TTS。
- 删除或替换 generated 槽位并移动旧 `Working/` 文件到 `assets/history/`。

权威刷新范围：

- 当前 StoryBoard 结构。
- 当前 Dialogue 的 `working_assets`。
- `Working/` 文件状态。
- `assets/images/`、`assets/videos/`、`assets/history/`。
- 当前 Dialogue 的 TTS 音频状态。
- History 版本列表和 manifest。

### 保存后的刷新

用户点击 Save 后：

- 系统保存当前 StoryBoard 结构。
- 如果本次保存只包含 Shot / Scene 结构变化，系统不得备份或清空 `Working/`，也不得清空仍存在 Dialogue 的 `working_assets` 路径。
- 只有本次保存实际删除 / 替换 generated 槽位，或删除 / 合并 Dialogue 导致某个 Dialogue 消失时，才移动对应 generated 文件到 `assets/history/`。
- 保存成功后，页面必须使用保存接口返回的权威结构刷新当前状态。
- 保存成功后清除 dirty 状态。
- 不应在保存成功后再触发额外的后台全量刷新。

### 生成 TTS 后的刷新

用户生成当前 Dialogue TTS 后：

- 系统必须将最终音频写入 `Working/`。
- 文件名必须使用当前 Dialogue 的 `dialogue_asset_key` 和 `Audio_Final` 槽位。
- 系统必须写回 `working_assets.audio.path`。
- 页面必须刷新当前 Dialogue 的音频状态、播放按钮状态和 Timeline 状态。
- 生成 TTS 不应触发 StoryBoard 全结构重载，除非后端返回了新的权威结构。

### 手动全量刷新

页面必须提供显式手动刷新入口，用于用户主动同步磁盘或后端中的外部变化。

手动刷新可以重新读取：

- StoryBoard 结构。
- `Working/`。
- Asset Pool 三个 Tab。
- History manifest。
- TTS 候选或 locked TTS manifest。

如果当前页面存在未保存编辑，用户点击手动刷新时必须二次确认。确认后才允许丢弃本地未保存状态并重新加载。

### 刷新策略总结

StoryBoard 页面不会自己轮询，也不会静默覆盖用户编辑。

所有会改变结构、素材、时间、绑定或 TTS 的用户动作完成后，都必须刷新相关 UI 区域；涉及文件落盘的动作以后端返回为准。

## 验收标准

### Shot / Scene 结构变化不清空素材

给定 `Working/` 中存在当前 Dialogue 的 generated 素材，当用户执行 Split Scene、Split Shot、Merge Scene 或 Merge Shot 并保存，且没有删除 Dialogue：

- 不创建新的 `regroup_{timestamp}` 或 history 批次目录。
- `SessionOutput/storyboard/Working/` 中的当前素材保持不变。
- 仍存在 Dialogue 的 `working_assets.*.path` 保持不变。
- 保存后的 StoryBoard 结构按新的 Shot / Scene 编排生效。

### 普通编辑不备份

给定 `Working/` 中存在当前素材，当用户只修改对白文字并保存：

- 不创建新的 history 版本目录。
- `Working/` 中的当前素材保持不变。
- `working_assets.*.path` 不因普通文本编辑被清空。

### History 复用

给定 History 中存在旧音频，当用户将旧音频复用到当前 Dialogue：

- 系统复制旧音频到 `SessionOutput/storyboard/Working/`。
- 新文件名使用目标 Dialogue 的 `dialogue_asset_key`。
- `working_assets.audio.path` 指向新的 Working 文件。
- history 原文件保持不变。

## 与 `Working/` 的关系

`Working/` 始终只代表当前 StoryBoard 中 Dialogue 级 generated 最终素材。

`assets/history/` 只保存从当前槽位移除的旧 generated 素材。它不能替代 `Working/`，也不能成为当前 `working_assets` 的最终路径来源。

因此，generated 最终素材路径必须满足：

```text
working_assets.*.path starts with "SessionOutput/storyboard/Working/"
```

原视频和上传素材绑定可以分别指向：

```text
SessionOutput/visual/srt_frames/...
SessionOutput/storyboard/assets/images/...
SessionOutput/storyboard/assets/videos/...
SessionOutput/storyboard/assets/audios/...
```

任何当前槽位都不能直接指向：

```text
SessionOutput/storyboard/assets/history/...
```
