# Analysis_V1 06_01_VideoPlanComposer 工具需求整理

版本：v0.1

状态：实现需求确认稿。本文用于指导 `06_01_VideoPlanComposer.py` 的实现与测试。

## 1. 背景

`05_01_VideoPlanGenerator.py` 负责把 StoryBoard 拆成可执行的 video segment 计划：

```text
SessionOutput/storyboard/video_generation_plan.json
```

`05_02_VideoPlanExecutor.py` 负责逐个执行 segment 级视频任务，产出每个 segment 的最终视频：

```text
SessionOutput/storyboard/Working/{first_dialogue_asset_key}_Video_Final.mp4
```

`06_01_VideoPlanComposer.py` 是后续独立拼接工具。它不和 `05_02` 混在一起，不负责图片、视频生成、TTS、对嘴型或音频替换。它只读取已经完成的 segment final video，并按 StoryBoard 层级逐级拼接：

```text
Segment Video_Final -> Scene_Final -> Shot_Final -> ShotPlan_Final
```

核心原则：

1. 选择 Scene 时，只拼该 Scene。
2. 选择 Shot 时，必须先逐个完成该 Shot 内所有 Scene 拼接，再把 Scene 结果拼成 Shot。
3. 选择 ShotPlan / Task 时，必须先逐个完成 Shot，再把 Shot 结果拼成 ShotPlan。
4. 严禁把所有 segment 小视频直接平铺拼成 Shot 或 ShotPlan。
5. 字幕使用 HyperFrame 处理，但 HyperFrame 第一版只负责字幕匹配和字幕渲染，不负责视频生成、不改画面内容、不重写剪辑逻辑。
6. 如果输入视频带水印，本工具需要执行去水印步骤；水印位置不固定，必须先探测，探测不到则不处理。

## 2. 工具定位

新增工具：

```text
06_01_VideoPlanComposer.py
```

推荐 Tool Use Session 步骤目录：

```text
S10_06_01_VideoPlanComposer/
```

当前主链路：

```text
S1_00_PrepareSessionVariables
S2_01_VideoProbeMetadata
S3_02_01_AudioASR
S4_02_02_VideoSRTFrame
S5_03_01_TTSBuilderG
S6_04_01_SRTRewrite
S7_04_02_StoryBoard
S8_05_01_VideoPlanGenerator
S9_05_02_VideoPlanExecutor
S10_06_01_VideoPlanComposer
```

边界：

1. 只做已完成视频的层级拼接。
2. 不生成图片。
3. 不生成视频。
4. 不生成 TTS。
5. 不调用对嘴型模型。
6. 不重新拆 Scene、Shot 或 Dialogue。
7. 不修改 `video_generation_plan.json` 的 segment 规划结构。
8. 不把字幕排版逻辑写回 StoryBoard；字幕工程和中间文件只保存在本工具目录。
9. 必须把 Scene / Shot / ShotPlan 最终视频同步到 `SessionOutput/storyboard/Working/`，供 UI 和后续发布工具使用。
10. 必须把 Scene / Shot / ShotPlan 的最终输出路径写回 StoryBoard JSON，供界面显示合并状态。

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

1. 只处理指定 Scene。
2. 按该 Scene 在 `video_generation_plan.json` 中的 `segments[]` 顺序读取每个 segment 的 `planned_outputs.video_path`。
3. 必须确认所有 segment final video 文件真实存在且非空。
4. 对 segment video 执行必要的去水印处理。
5. 按 segment 顺序拼成 Scene 无字幕版。
6. 使用 HyperFrame 将该 Scene 对应字幕匹配并渲染到 Scene 视频上。
7. 输出 `Scene_Final` 和可选的 `Scene_Subtitled_Final`。

### 3.2 Shot 模式

输入指定：

```text
--target-type shot
--shot-id shot_001
```

行为：

1. 按 Shot 内 `scenes[]` 顺序逐个执行 Scene 模式。
2. 每个 Scene 完成后产出 `Scene_Final`。
3. 只有所有可执行 Scene 都完成后，才把该 Shot 下的 `Scene_Final` 依序拼成 `Shot_Final`。
4. 如果某个 Scene blocked / failed，Shot 拼接不能平铺跳过该 Scene；已确认 Shot 整体 blocked，不输出局部 Shot 成片。
5. Shot 级字幕策略已确认：使用各 Scene 已带字幕视频拼接，不在 Shot 级重复渲染字幕。

### 3.3 Task / ShotPlan 模式

输入指定：

```text
--target-type task
```

含义：

```text
当前 workspace 的整个 SessionOutput/storyboard/video_generation_plan.json
```

行为：

1. 按 `shots[]` 顺序逐个执行 Shot 模式。
2. 每个 Shot 必须先完成内部 Scene 拼接。
3. 每个 Shot 完成后产出 `Shot_Final`。
4. 只有所有可执行 Shot 都完成后，才把 `Shot_Final` 依序拼成 `ShotPlan_Final`。
5. 严禁把全片所有 segment video 直接拼成最终视频。
6. ShotPlan 级字幕策略已确认：使用各 Scene 已带字幕视频经过 Shot 拼接后的结果，不在 ShotPlan 级重复渲染字幕。

## 4. 输入

### 4.1 必需输入

prepare 阶段从 workspace 读取：

```text
SessionContext/Variables.json
SessionOutput/storyboard/srt_storyboard.json
SessionOutput/storyboard/video_generation_plan.json
```

复制到本工具：

```text
S10_06_01_VideoPlanComposer/Working/InputFrom_0_Variables.json
S10_06_01_VideoPlanComposer/Working/InputFrom_7_srt_storyboard.json
S10_06_01_VideoPlanComposer/Working/InputFrom_8_video_generation_plan.json
S10_06_01_VideoPlanComposer/Working/InputParams_video_plan_composer.json
```

### 4.2 必需媒体输入

每个要拼接的 segment 必须有最终视频：

```text
segment.planned_outputs.video_path
```

对应文件必须：

1. 存在。
2. 非空。
3. 可被 ffmpeg / ffprobe 读取。
4. 与当前 `video_generation_plan.json` 的 segment 归属一致。

### 4.3 字幕输入

第一版推荐字幕来源优先级：

1. `srt_storyboard.json` 中当前 Scene 的 Dialogue 文本和时间。
2. 如果存在改写后 SRT 对齐字段，则优先使用 `04_01` / `04_02` 已确认的改写文本。
3. 如果某个 Dialogue 文本为空，则该 Dialogue 不生成字幕，但必须在 Report 中 warning。

已确认：

1. 字幕文本使用 StoryBoard 中用于生成 TTS 的字段，不使用原始 SRT 文本。
2. 字幕时间使用 05-02 后 segment final video 的真实时长重新累加。

第一版执行口径：字幕文本使用 StoryBoard/TTS 同源文本；字幕时间使用拼接后 Scene 内相对时间，由 segment final video 的真实时长和 Dialogue 在 segment 内的相对比例换算。

## 5. 参数

建议参数：

```text
--workspace <workspace>
--target-type scene|shot|task
--shot-id <shot_id>
--scene-id <scene_id>
--subtitle-mode hyperframe|none
--watermark-mode auto|always|never
--force
--resume
--print-json
```

参数规则：

1. `--target-type scene` 必须提供 `--shot-id` 和 `--scene-id`。
2. `--target-type shot` 必须提供 `--shot-id`，不得要求 `--scene-id`。
3. `--target-type task` 不需要 `--shot-id` 或 `--scene-id`，含义与 `05_01` 一致：当前 Session 的整个 StoryBoard / ShotPlan。
4. `--subtitle-mode hyperframe` 表示用 HyperFrame 匹配并渲染字幕。
5. `--subtitle-mode none` 表示只做无字幕拼接。
6. `--watermark-mode auto` 表示只在检测到水印或配置要求时去水印。
7. `--watermark-mode always` 表示所有输入视频都执行去水印；第一版默认值已确认为 `always`。
8. `--watermark-mode never` 表示不执行去水印。

## 6. 目录合同

### 6.1 工具目录

本工具必须创建三个一级目录：

```text
S10_06_01_VideoPlanComposer/
  Working/
  Output/
  Report/
```

规则：

1. 第一版不调用 LLM / VLM，不创建 `Prompt/`。
2. `Working/` 保存输入快照、拼接清单、ffmpeg concat list、HyperFrame 字幕工程临时文件和中间视频。
3. `Output/` 只保存最终业务产物：Scene / Shot / ShotPlan 视频、字幕文件、compose result JSON。
4. `Report/` 保存 `Result.json`、执行状态、blocked / failed 原因和审计信息。
5. 如果 HyperFrame 必须使用多文件工程目录，允许在 `Working/` 下创建受控子目录：`HyperFrame_{scope_key}/`。该子目录只保存本 scope 的字幕渲染工程，不进入 `Output/` 或 `SessionOutput`。

### 6.2 SessionOutput 输出目录

本工具同步最终业务素材到：

```text
SessionOutput/storyboard/Working/
```

建议命名：

```text
SessionOutput/storyboard/Working/{scene_key}_Scene_Final.mp4
SessionOutput/storyboard/Working/{scene_key}_Scene_Subtitled_Final.mp4
SessionOutput/storyboard/Working/{scene_key}_SceneComposeManifest.json

SessionOutput/storyboard/Working/{shot_id}_Shot_Final.mp4
SessionOutput/storyboard/Working/{shot_id}_Shot_Subtitled_Final.mp4
SessionOutput/storyboard/Working/{shot_id}_ShotComposeManifest.json

SessionOutput/storyboard/Working/ShotPlan_Final.mp4
SessionOutput/storyboard/Working/ShotPlan_Subtitled_Final.mp4
SessionOutput/storyboard/Working/ShotPlanComposeManifest.json
```

已确认：

1. `scene_key` 使用 `{shot_id}_{scene_id}`，例如 `shot_001_scene_002_Scene_Final.mp4`。
2. 同时保留无字幕版和带字幕版。
3. 所有最终业务输出同步到 `SessionOutput/storyboard/Working/`。

第一版规则：同时保留无字幕版和带字幕版；如果 `subtitle-mode=none`，只生成无字幕版。

## 7. 层级拼接规则

### 7.1 Segment -> Scene

1. 按 `video_generation_plan.json` 中 Scene 的 `segments[]` 顺序拼接。
2. 输入只接受 `planned_outputs.video_path` 指向的 final video。
3. 不读取 raw video、lip sync 临时视频、audio synced 临时视频或上传源视频。
4. 拼接前必须 ffprobe 每个输入视频，记录时长、分辨率、帧率、音轨存在情况。
5. 如果分辨率、编码或帧率不一致，第一版建议统一转码到标准竖屏 9:16、H.264、AAC。
6. 如果某段缺失，Scene blocked；不得静默跳过 segment。

### 7.2 Scene -> Shot

1. 输入只接受本工具刚生成或已可信存在的 `Scene_Final` / `Scene_Subtitled_Final`。
2. 不允许直接读取 segment video 拼 Shot。
3. Scene 顺序以 `video_generation_plan.json` 中 Shot 的 `scenes[]` 顺序为准。
4. 如果某 Scene blocked，Shot 整体 blocked；不得直接跳过该 Scene，也不输出局部 Shot 成片。

### 7.3 Shot -> ShotPlan

1. 输入只接受本工具刚生成或已可信存在的 `Shot_Final` / `Shot_Subtitled_Final`。
2. 不允许直接读取 segment video 拼 ShotPlan。
3. Shot 顺序以 `video_generation_plan.json` 顶层 `shots[]` 顺序为准。
4. 如果某 Shot blocked，ShotPlan 整体 blocked；不得直接跳过该 Shot，也不输出局部 ShotPlan 成片。

## 8. 去水印规则

第一版需求：

1. 如果视频有水印，需要去掉水印。
2. 去水印不能破坏字幕、人物、产品或主体画面。
3. 去水印前后都必须记录输入输出路径和处理策略。
4. 去水印失败时，不得发布半成品覆盖已有 Scene / Shot / ShotPlan 输出。

已确认：

1. 第一版默认 `watermark-mode=always`。
2. 水印位置不固定，必须探测。
3. 去水印在每个 segment 拼 Scene 前执行。

第一版建议：

1. 每个 segment final video 进入 Scene 拼接前先执行水印探测。
2. 探测结果必须记录水印区域、置信度和处理策略。
3. 如果探测不到水印但 `watermark-mode=always`，记录 warning 后不做水印处理，继续后续拼接。
4. 去水印策略按探测结果自动选择；第一版可以先使用安全的局部修复 / delogo 类策略，后续再扩展更复杂算法。

## 9. HyperFrame 字幕规则

HyperFrame 只负责字幕匹配和字幕渲染。

必须遵守：

1. 不用 HyperFrame 重新生成视频内容。
2. 不用 HyperFrame 改变镜头顺序。
3. 不用 HyperFrame 调整 segment 时长。
4. 不在 HyperFrame 中重新配音或改音频。
5. 只把已拼接视频作为背景媒体，叠加时间匹配后的字幕。

字幕输出建议：

```text
S10_06_01_VideoPlanComposer/Output/{scene_key}_Scene_Subtitles.srt
S10_06_01_VideoPlanComposer/Output/{scene_key}_Scene_Subtitles.json
S10_06_01_VideoPlanComposer/Output/{scene_key}_Scene_Subtitled_Final.mp4
```

字幕样式第一版规则：

1. 字幕样式优先沿用 StoryBoard / Koubo UI 的默认口播字幕风格。
2. 第一版使用普通整句字幕，不做逐字高亮。
3. 字幕只在 Scene 级渲染一次，Shot / ShotPlan 只拼接带字幕 Scene。

已确认：

1. Scene 级渲染字幕。
2. Shot / ShotPlan 只拼接已经带字幕的 Scene，不重复渲染字幕，避免时间轴二次漂移。

第一版建议：

1. 普通整句字幕。

## 10. 输出合同

### 10.1 工具 Output

```text
S10_06_01_VideoPlanComposer/Output/video_plan_compose_result.json
S10_06_01_VideoPlanComposer/Output/{scene_key}_Scene_Final.mp4
S10_06_01_VideoPlanComposer/Output/{scene_key}_Scene_Subtitled_Final.mp4
S10_06_01_VideoPlanComposer/Output/{shot_id}_Shot_Final.mp4
S10_06_01_VideoPlanComposer/Output/{shot_id}_Shot_Subtitled_Final.mp4
S10_06_01_VideoPlanComposer/Output/ShotPlan_Final.mp4
S10_06_01_VideoPlanComposer/Output/ShotPlan_Subtitled_Final.mp4
```

只输出当前 target 范围需要的文件。Scene 模式不输出 Shot / ShotPlan。Shot 模式不输出 ShotPlan。

### 10.2 SessionOutput

```text
SessionOutput/storyboard/video_plan_compose_result.json
SessionOutput/storyboard/Working/{scene_key}_Scene_Final.mp4
SessionOutput/storyboard/Working/{scene_key}_Scene_Subtitled_Final.mp4
SessionOutput/storyboard/Working/{shot_id}_Shot_Final.mp4
SessionOutput/storyboard/Working/{shot_id}_Shot_Subtitled_Final.mp4
SessionOutput/storyboard/Working/ShotPlan_Final.mp4
SessionOutput/storyboard/Working/ShotPlan_Subtitled_Final.mp4
```

成功覆盖 `SessionOutput/storyboard/Working/` 中同名文件前，必须备份到：

```text
SessionOutput/storyboard/assets/history/batch_*_06_01_overwrite_backup/
```

### 10.3 StoryBoard JSON 写回

已确认：本工具需要写回 StoryBoard JSON，这样界面可以看到 Scene / Shot / Task 合并状态。

必须同步更新：

```text
SessionOutput/storyboard/srt_storyboard.json
SessionOutput/storyboard/koubo_storyboard_edit.json   # 如果存在
```

建议写回位置：

```json
{
  "compose_assets": {
    "scene": {
      "status": "completed",
      "video_path": "SessionOutput/storyboard/Working/shot_001_scene_001_Scene_Final.mp4",
      "subtitled_video_path": "SessionOutput/storyboard/Working/shot_001_scene_001_Scene_Subtitled_Final.mp4",
      "manifest_path": "SessionOutput/storyboard/Working/shot_001_scene_001_SceneComposeManifest.json",
      "updated_at": "2026-06-01T00:00:00Z"
    }
  }
}
```

写回规则：

1. Scene 输出写回对应 Scene 节点。
2. Shot 输出写回对应 Shot 节点。
3. Task / ShotPlan 输出写回 StoryBoard 顶层。
4. 如果 `srt_storyboard.json` 和 `koubo_storyboard_edit.json` 同时存在，必须同步写入两份；任一写回失败，本次 finalize 标记 failed 或 warning，不能静默成功。
5. 写回前必须备份旧 JSON 到 `SessionOutput/storyboard/assets/history/batch_*_06_01_overwrite_backup/`。
6. 写回只记录合并状态和输出路径，不写入 HyperFrame 工程细节，不写入水印探测临时数据。

## 11. 执行结果 JSON

`video_plan_compose_result.json` 至少包含：

```json
{
  "tool": "06_01_VideoPlanComposer",
  "tool_version": "0.1.0",
  "status": "completed",
  "target": {
    "target_type": "shot",
    "shot_id": "shot_001",
    "scene_id": ""
  },
  "source_plan_hash": "sha256...",
  "settings": {
    "subtitle_mode": "hyperframe",
    "watermark_mode": "auto"
  },
  "scenes": [],
  "shots": [],
  "shot_plan": {},
  "created_files": [],
  "backups": [],
  "warnings": [],
  "blocked_reasons": [],
  "updated_at": "2026-06-01T00:00:00Z"
}
```

Scene 结果建议：

```json
{
  "scene_id": "scene_001",
  "shot_id": "shot_001",
  "status": "completed",
  "input_segments": [
    {
      "segment_id": "shot_001_scene_001_segment_001",
      "video_path": "SessionOutput/storyboard/Working/srt_0001_Video_Final.mp4",
      "duration_seconds": 4.62
    }
  ],
  "outputs": {
    "scene_video_path": "SessionOutput/storyboard/Working/shot_001_scene_001_Scene_Final.mp4",
    "scene_subtitled_video_path": "SessionOutput/storyboard/Working/shot_001_scene_001_Scene_Subtitled_Final.mp4",
    "subtitle_srt_path": "SessionOutput/storyboard/Working/shot_001_scene_001_Scene_Subtitles.srt"
  }
}
```

## 12. Rerun 和 Resume

### 12.1 原始状态

原始状态：

1. `S10_06_01_VideoPlanComposer/` 不存在，或其中目录为空。
2. 本工具本次 run 尚未产生新的 `Output/video_plan_compose_result.json`。
3. `SessionOutput/storyboard/Working/` 可能已有旧 Scene / Shot / ShotPlan 输出；这些文件不属于 `--force` 清理范围。

### 12.2 Force

`--force` 行为：

1. 清理并重建 `S10_06_01_VideoPlanComposer/Working/`。
2. 清理并重建 `S10_06_01_VideoPlanComposer/Output/`。
3. 清理并重建 `S10_06_01_VideoPlanComposer/Report/`。
4. 不删除 `SessionOutput/storyboard/Working/` 中任何文件。
5. 成功覆盖同名 Scene / Shot / ShotPlan 输出前，必须先备份旧文件。

### 12.3 Resume

`--resume` 行为：

1. 必须重新读取 `video_generation_plan.json` 并计算 `source_plan_hash`。
2. 必须确认输入 segment video 文件仍存在且 hash / mtime / size 与上次记录一致。
3. 已完成 Scene 只有在所有输入 segment video 未变化、输出文件存在且可 ffprobe 时才能复用。
4. 已完成 Shot 只有在所有输入 Scene 输出未变化时才能复用。
5. 已完成 ShotPlan 只有在所有输入 Shot 输出未变化时才能复用。
6. 任一层级输入变化，必须从该层级开始重新拼接，并递归影响上级输出。

## 13. 失败和局部继续策略

1. 缺少 `video_generation_plan.json`：工具级 `blocked`。
2. 缺少 `srt_storyboard.json`：工具级 `blocked`。
3. 目标 Scene / Shot 不存在：工具级 `blocked`。
4. Scene 内任一 segment final video 缺失：该 Scene `blocked`。
5. Scene blocked 时，依赖它的 Shot 整体 blocked。
6. Shot blocked 时，依赖它的 ShotPlan 整体 blocked。
7. 去水印失败：对应 Scene failed，不发布该 Scene 输出。
8. HyperFrame 字幕渲染失败：如果 `subtitle-mode=hyperframe`，对应 Scene failed，不降级输出无字幕版充当成功。
9. ffmpeg 拼接失败：对应层级 failed，不发布半成品。

已确认：不静默跳过失败 Scene / Shot；允许已经完成的下级产物保留，但上级产物必须整体 blocked。

## 14. 测试要求

### 14.1 单元测试

建议测试：

1. `test_scene_target_requires_shot_and_scene_id`
2. `test_shot_target_requires_shot_id`
3. `test_task_target_accepts_full_plan`
4. `test_scene_composes_segments_in_plan_order`
5. `test_shot_composes_scene_outputs_not_segments`
6. `test_task_composes_shot_outputs_not_segments`
7. `test_missing_segment_video_blocks_scene`
8. `test_missing_scene_output_blocks_shot`
9. `test_missing_shot_output_blocks_shot_plan`
10. `test_resume_reuses_scene_when_inputs_unchanged`
11. `test_resume_rebuilds_scene_when_segment_video_changes`
12. `test_force_cleans_tool_dirs_but_not_storyboard_working`
13. `test_overwrite_backs_up_existing_scene_shot_outputs`
14. `test_subtitle_mode_none_skips_hyperframe`
15. `test_subtitle_mode_hyperframe_emits_scene_subtitle_files`
16. `test_watermark_mode_never_skips_watermark_removal`
17. `test_result_json_has_no_sensitive_strings`

### 14.2 集成测试

集成测试使用最小 workspace：

```text
SessionContext/Variables.json
SessionOutput/storyboard/srt_storyboard.json
SessionOutput/storyboard/video_generation_plan.json
SessionOutput/storyboard/Working/*_Video_Final.mp4
```

覆盖：

1. Scene 模式拼接单 Scene。
2. Shot 模式先拼 Scene 再拼 Shot。
3. Task 模式先拼 Shot 再拼 ShotPlan。
4. HyperFrame 字幕输出文件存在且视频可播放。
5. 删除某个 segment video 后工具 blocked，且不发布半成品。

### 14.3 手工验收

1. UI 或文件系统能看到 Scene 输出。
2. Shot 输出由 Scene 输出拼接而来，不是 segment 平铺。
3. ShotPlan 输出由 Shot 输出拼接而来，不是 segment 平铺。
4. 字幕时间和最终视频内容基本同步。
5. 去水印开启时，水印区域被处理且主体画面未明显损坏。

## 15. 工具通用问题逐条回答

| 通用问题 | `06_01_VideoPlanComposer` 第一版建议答案 |
|---|---|
| 是否最小程度生成中间文件和产出物？ | 是。只生成 Working 输入快照、必要 concat / subtitle / HyperFrame 临时文件、Output 最终视频和 JSON、Report/Result.json。 |
| 是否需要连接数据库？ | 否。只读取 workspace 内的 `SessionContext`、StoryBoard、video plan 和已完成视频文件。 |
| 是否需要产出或更新 SessionContext？ | 否。拼接结果是 StoryBoard 业务产物，不写 `SessionContext/Variables.json`。 |
| 是否需要写回 StoryBoard JSON？ | 是。写回 Scene / Shot / Task 的合并状态和输出路径，供界面展示。 |
| 本工具产出物是什么，给后面哪一步使用？ | 产出 Scene / Shot / ShotPlan 拼接视频、写回 StoryBoard 合并状态，并产出 `video_plan_compose_result.json`，供 UI 预览、人工验收和后续发布工具使用。 |
| 如果产出物缺失，下游应该 blocked、fallback 还是 warning？ | 发布 / 导出工具必须 blocked；UI 可 warning 并提示先运行 `06_01`。 |
| 是否按照 Rerun 和断点继续实现？ | 是。按 source plan hash、target params、输入视频文件 hash/size/mtime、输出文件 ffprobe 结果判断复用。 |
| 原始状态是什么？ | `S10_06_01_VideoPlanComposer/` 不存在或为空，且本次没有新的 compose result。 |
| 强制 Rerun 如何恢复原始状态？ | 只清理本工具 Working / Output / Report，不删除 StoryBoard Working 中旧结果；覆盖前先备份。 |
| 断点续跑如何识别已完成且可信的子步骤？ | Scene / Shot / ShotPlan 分层记录输入清单和 hash。下级输入不变且输出可 ffprobe 才复用。 |
| 是否需要 Prompt 目录？ | 不需要。第一版不调用模型；HyperFrame 字幕工程放入 Working。 |

## 16. 已确认问题

### 16.1 本轮已确认

| 问题 | 已确认答案 |
|---|---|
| 工具文件名 | `06_01_VideoPlanComposer.py` |
| Step 目录 | `S10_06_01_VideoPlanComposer/` |
| `target-type` | 与 05-01 一致，使用 `scene|shot|task`；`task` 表示当前 Session 的整个 StoryBoard / ShotPlan。 |
| 是否保留无字幕版和带字幕版 | 保留两份。 |
| 字幕文本来源 | 使用 StoryBoard 中用于生成 TTS 的字段。 |
| 字幕时间 | 以 05-02 后 segment final video 真实时长重新累加。 |
| HyperFrame 字幕层级 | 只在 Scene 级渲染字幕；Shot / ShotPlan 只拼接带字幕 Scene。 |
| 去水印默认策略 | `watermark-mode=always`。 |
| 水印位置 | 不固定，必须探测。 |
| 去水印执行时机 | 每个 segment 进入 Scene 拼接前执行。 |
| 去水印策略 | 按探测区域自动选择策略。 |
| 探测不到水印 | 不处理该视频，继续拼接。 |
| HyperFrame 字幕失败策略 | 直接 failed，不降级输出无字幕版充当成功。 |
| 下级 blocked 策略 | Shot / ShotPlan 整体 blocked，不输出局部上级成片。 |
| StoryBoard 写回 | 写回 Scene / Shot / Task 合并状态和输出路径，让界面可见。 |
| StoryBoard 写回字段 | 使用 `compose_assets`。 |
| UI 状态展示 | 暂时不改 UI。 |

### 16.2 实现备注

当前业务问题已确认完毕。实现过程中如果发现现有 StoryBoard UI 已有更合适的合并状态字段，可以再单独提出兼容方案，但第一版按 `compose_assets` 执行，不改 UI。
