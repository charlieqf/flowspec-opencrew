# StoryBoard Working 运行与失败状态一致性需求设计

## 1. 核心原则

StoryBoard 的唯一事实依据必须是 `SessionOutput/storyboard/Working/` 下的一级文件。

Image Plan、Video Plan、Video Only Plan 不是三套独立状态体系，而是同一个 StoryBoard 大 Plan 在不同入口下的子集视图。用户点击任一 Plan 时，后端都应该从 Working 一级文件和 StoryBoard JSON 重新推导当前状态，而不是维护三套互相分离的运行态。

因此，本需求不再引入 `_ExecutionStatus/` 子目录，也不按 `image_plan / video_plan / video_only_plan` 分文件夹。运行中和失败状态同样落在 Working 一级文件里，和业务文件采用同一套稳定命名规则。

## 2. 统一大 Plan

StoryBoard Working 对应的是一条统一的 Segment 生产链路：

```text
音频提示词
音频
原图
图像提示词
尾帧拷贝新图
新图
视频提示词
新视频
新视频尾帧
拷贝为终视频
拷贝终视频尾帧
对嘴型
对嘴型尾帧
声音合成
声音合成尾帧
终视频
终视频尾帧
```

三个 Plan 页面只是这条链路的不同过滤视图：

1. Image Plan：关注原图、图像提示词、新图，以及尾帧作为新图来源的状态。
2. Video Only Plan：关注新图、视频提示词、新视频、拷贝为终视频、终视频尾帧。
3. Video Plan：关注音频、图像、视频、对嘴型、声音合成、终视频、尾帧，以及跨 Segment 尾帧继承。

颜色状态必须由统一链路推导。不能出现 Image Plan 认为某槽位红色，而 Video Plan 因为没读同一状态标记文件显示白色的情况。

## 3. Working 一级文件约定

Working 下继续保持一级平铺结构，不新增状态子目录。

业务文件沿用当前约定：

```text
SessionOutput/storyboard/Working/{segment_anchor}_AudioPrompt.json
SessionOutput/storyboard/Working/{segment_anchor}_Audio_Final.wav
SessionOutput/storyboard/Working/{segment_anchor}_Image_Source.png
SessionOutput/storyboard/Working/{segment_anchor}_ImagePrompt.json
SessionOutput/storyboard/Working/{segment_anchor}_Image_New.png
SessionOutput/storyboard/Working/{segment_anchor}_VideoPrompt.json
SessionOutput/storyboard/Working/{segment_anchor}_Video_Raw.mp4
SessionOutput/storyboard/Working/{segment_anchor}_Video_Final.mp4
SessionOutput/storyboard/Working/{segment_anchor}_TailFrame.png
```

运行 / 失败状态标记文件也在同一层。热路径状态应放在文件名上，而不是只放在 JSON 内容里：

```text
SessionOutput/storyboard/Working/{segment_anchor}_{stage}_Running_{signature12}_{marker_uid}.json
SessionOutput/storyboard/Working/{segment_anchor}_{stage}_Failed_{signature12}_{marker_uid}.json
```

示例：

```text
SessionOutput/storyboard/Working/srt_0001_01_Image_New_Running_a1b2c3d4e5f6_mk7q9p2x.json
SessionOutput/storyboard/Working/srt_0001_01_Video_Raw_Failed_b2c3d4e5f6a1_mk8r1n4c.json
SessionOutput/storyboard/Working/srt_0001_01_Video_Final_Copy_Running_c3d4e5f6a1b2_mk9t2d7e.json
SessionOutput/storyboard/Working/srt_0001_01_TailFrame_Failed_d4e5f6a1b2c3_mk4h6s8v.json
```

`signature12` 是当前 stage `step_signature` 的前 12 位，用于让颜色合成在不读取 JSON 内容的情况下快速判断这个运行 / 失败标记是否仍属于当前输入。

`marker_uid` 是状态文件的唯一 ID，必须在创建 Running 标记时生成，并在 Running 改名为 Failed、移动到归档时保持不变。它可以来自 `attempt_id` 的短 hash，也可以是短随机 ID。因为文件名本身已经唯一，归档时不要求额外加时间戳；时间信息保存在 JSON 字段里。

状态标记文件不是素材文件。素材扫描、素材库展示、拼接工具、历史素材恢复必须忽略 `*_Running_*.json` 和 `*_Failed_*.json`。

执行归档允许在 Working 下使用一个专用文件夹。归档不能做成大 JSON，不能把多次执行合并成一个文件；必须保留每次执行对应的状态标记文件本体。

```text
SessionOutput/storyboard/Working/ExecutionArchive/
```

Running / Failed 标记用于当前颜色热路径；`ExecutionArchive/` 只保存历史状态标记文件，用于追溯历史执行、恢复外部 API 任务、排查文件丢失。产出媒体文件仍按现有规则进入 history；状态归档只保存状态 JSON，不复制媒体产物。归档移动必须避免覆盖；正常情况下由 `marker_uid` 保证唯一，极端碰撞时才允许追加很短的去重后缀。

## 4. Segment Anchor 命名稳定性

状态标记文件和业务文件都必须使用同一个稳定前缀：`segment_anchor`。

`segment_anchor` 的职责是把 Plan 里的 Segment 列、StoryBoard 的 Dialogue/SRT、Working 文件名稳定绑定起来。它不能跟随临时 segment 序号随意变化，否则刷新计划、拆分 Segment、合并 Segment 后无法正确匹配已有文件。

### 4.1 推荐规则

1. Dialogue/SRT 存在时，`segment_anchor` 优先使用该 Segment 的首个代表 Dialogue 的 `dialogue_asset_key`。
2. `dialogue_asset_key` 优先由稳定 `srt_id` 规范化得到，例如 `srt_0001_01`。
3. 如果一个 Segment 覆盖多个 Dialogue，使用首个代表 Dialogue 的 key 作为 Segment 文件前缀，同时在状态标记文件中记录完整 `dialogue_asset_keys`。
4. 如果是空镜、产品特写、无对白 Segment，使用稳定的 `segment_id` 派生 key，例如 `shot_001_scene_002_segment_003`，但一旦生成并落盘，就必须进入 anchor 映射表，不再随重建变化。
5. 不允许使用临时数组下标、Plan 生成时间、前端行号作为文件名前缀。

### 4.2 Anchor 映射

为了处理后续 Segment 拆分、合并、调整覆盖范围，Working 需要一个一级映射文件：

```text
SessionOutput/storyboard/Working/StoryBoardSegmentAnchors.json
```

示例：

```json
{
  "schema_version": "storyboard_segment_anchors_0.1",
  "anchors": {
    "srt_0001_01": {
      "segment_anchor": "srt_0001_01",
      "current_segment_id": "shot_001_scene_001_segment_001",
      "dialogue_asset_keys": ["srt_0001_01", "srt_0001_02"],
      "representative_dialogue_asset_key": "srt_0001_01",
      "anchor_source": "first_dialogue",
      "created_at": "2026-06-22T10:00:00Z",
      "updated_at": "2026-06-22T10:05:00Z"
    }
  }
}
```

Plan 重建时先读取这个映射：

1. 如果当前 Segment 仍包含已有代表 Dialogue，复用旧 `segment_anchor`。
2. 如果 Segment 拆分，包含原代表 Dialogue 的新 Segment 继承旧 anchor；其他新 Segment 用各自首个 Dialogue 生成新 anchor。
3. 如果 Segment 合并，优先继承最靠前代表 Dialogue 的 anchor，并把被合并 anchors 记录为 aliases。
4. 如果空镜 Segment 的 `segment_id` 未变，复用旧 anchor；如果被移动到其他 Scene/Shot，但语义实体未变，需要通过映射保留旧 anchor。

## 5. Stage 与文件名

统一大 Plan 的 stage 需要稳定命名，并尽量贴近已有业务文件后缀。

| Stage | 业务文件 | 状态标记文件 | 说明 |
| --- | --- | --- | --- |
| `AudioPrompt` | `{anchor}_AudioPrompt.json` | `{anchor}_AudioPrompt_{Running/Failed}_{sig}_{uid}.json` | 音频提示词 |
| `Audio_Final` | `{anchor}_Audio_Final.wav` | `{anchor}_Audio_Final_{Running/Failed}_{sig}_{uid}.json` | Dialogue 级最终音频 |
| `SegmentAudio_Final` | `{anchor}_SegmentAudio_Final.wav` | `{anchor}_SegmentAudio_Final_{Running/Failed}_{sig}_{uid}.json` | Segment 级合成音频 |
| `Image_Source` | `{anchor}_Image_Source.*` | `{anchor}_Image_Source_{Running/Failed}_{sig}_{uid}.json` | 原图或绑定首帧 |
| `ImagePrompt` | `{anchor}_ImagePrompt.json` | `{anchor}_ImagePrompt_{Running/Failed}_{sig}_{uid}.json` | 图像提示词 |
| `Image_New` | `{anchor}_Image_New.*` | `{anchor}_Image_New_{Running/Failed}_{sig}_{uid}.json` | 新图；可以由图像生成或尾帧拷贝生成 |
| `VideoPrompt` | `{anchor}_VideoPrompt.json` | `{anchor}_VideoPrompt_{Running/Failed}_{sig}_{uid}.json` | 视频提示词 |
| `Video_Raw` | `{anchor}_Video_Raw.mp4` | `{anchor}_Video_Raw_{Running/Failed}_{sig}_{uid}.json` | 新生成视频 |
| `Video_Raw_TailFrame` | `{anchor}_Video_Raw_TailFrame.png` | `{anchor}_Video_Raw_TailFrame_{Running/Failed}_{sig}_{uid}.json` | Raw 诊断尾帧 |
| `Video_Final_Copy` | `{anchor}_Video_Final.mp4` | `{anchor}_Video_Final_Copy_{Running/Failed}_{sig}_{uid}.json` | Raw 或手动视频拷贝成终视频 |
| `Video_Final_LipSync` | `{anchor}_Video_Final.mp4` | `{anchor}_Video_Final_LipSync_{Running/Failed}_{sig}_{uid}.json` | 对嘴型生成终视频 |
| `Video_Final_AudioMix` | `{anchor}_Video_Final.mp4` | `{anchor}_Video_Final_AudioMix_{Running/Failed}_{sig}_{uid}.json` | 声音合成或替换生成终视频 |
| `Video_Final` | `{anchor}_Video_Final.mp4` | `{anchor}_Video_Final_{Running/Failed}_{sig}_{uid}.json` | 当前终视频槽位 |
| `TailFrame` | `{anchor}_TailFrame.png` | `{anchor}_TailFrame_{Running/Failed}_{sig}_{uid}.json` | 当前终视频尾帧，下游消费凭证 |

同一个业务文件可以由多个 stage 产出，例如 `Video_Final_Copy`、`Video_Final_LipSync`、`Video_Final_AudioMix` 都可能写入 `{anchor}_Video_Final.mp4`。因此状态标记文件必须区分“执行方式”，但最终绿色仍只认业务文件和 StoryBoard JSON 绑定。

## 6. 状态标记文件与 JSON 内容

每个状态标记文件是一个 Working 一级 JSON。文件名承担颜色热路径判断；JSON 内容承担详情、审计和完整签名校验。

```json
{
  "schema_version": "storyboard_working_marker_0.1",
  "segment_anchor": "srt_0001_01",
  "current_segment_id": "shot_001_scene_001_segment_001",
  "dialogue_asset_keys": ["srt_0001_01"],
  "representative_dialogue_asset_key": "srt_0001_01",
  "stage": "Video_Raw",
  "marker_state": "running",
  "source_view": "video_only_plan",
  "step_signature": "sha256...",
  "step_signature_prefix": "a1b2c3d4e5f6",
  "marker_uid": "mk7q9p2x",
  "job_id": "exec_1770000000000",
  "attempt_id": "exec_1770000000000:srt_0001_01:Video_Raw:1",
  "external_api_tasks": [
    {
      "provider": "wan",
      "api_name": "video_generation",
      "external_task_id": "wan_task_abc123",
      "external_request_id": "req_abc123",
      "status_url": "",
      "created_at": "2026-06-22T10:00:01Z",
      "last_polled_at": "",
      "last_known_status": "submitted",
      "result_urls": [],
      "raw_response_ref": "SessionOutput/storyboard/Working/srt_0001_01_Video_Raw_Running_a1b2c3d4e5f6_mk7q9p2x.json"
    }
  ],
  "started_at": "2026-06-22T10:00:00Z",
  "updated_at": "2026-06-22T10:00:05Z",
  "failed_at": "",
  "heartbeat_at": "2026-06-22T10:00:05Z",
  "input_paths": [
    "SessionOutput/storyboard/Working/srt_0001_01_Image_New.png",
    "SessionOutput/storyboard/Working/srt_0001_01_VideoPrompt.json"
  ],
  "output_paths": [
    "SessionOutput/storyboard/Working/srt_0001_01_Video_Raw.mp4"
  ],
  "binding_paths": [],
  "overwrites_existing_output": false,
  "error": {
    "code": "",
    "message": ""
  }
}
```

字段规则：

1. `segment_anchor + stage + marker_state + step_signature_prefix + marker_uid` 定位一个状态标记文件。
2. 同一 `segment_anchor + stage` 同一时刻最多只能有一个当前匹配的 Running 或 Failed 标记。
3. `marker_state` 必须和文件名中的 `Running` 或 `Failed` 一致。
4. `source_view` 只记录这次执行从哪个页面入口触发，用于审计；不能参与状态隔离。
5. `step_signature_prefix` 必须等于完整 `step_signature` 的前 12 位。
6. `marker_uid` 必须和文件名最后一段一致，并在同一次 attempt 生命周期内保持不变。
7. `Running` 文件存在代表黄色候选；`Failed` 文件存在代表红色候选。
8. 绿色不需要 Completed 标记，仍只由业务文件存在、内容有效、StoryBoard JSON 绑定一致决定。
9. 失败 JSON 必须包含可展示给用户的脱敏错误摘要。
10. `external_api_tasks` 必须记录当前 stage 已经创建的外部 API 任务 ID；如果一个 stage 调用了多个上游 API，按创建顺序追加。
11. 外部 API 返回任务 ID 后，必须立即更新 Running 标记；不能等本地执行结束后再写。
12. `external_api_tasks[].external_task_id` 是恢复上游任务的关键字段，失败、stale、进程重启后都必须保留到归档。

### 6.1 性能约定

颜色合成的高频路径只做三类轻操作：

1. 扫描 Working 一级文件名。
2. 判断业务文件是否存在、大小是否大于 0、绑定是否一致。
3. 用文件名里的 `Running / Failed / signature12` 判断黄 / 红候选；`marker_uid` 只用于唯一性和归档防覆盖。

只有以下场景需要读取标记 JSON 内容：

1. 当前文件名 signature 前缀匹配，需要展示错误详情或执行详情。
2. Running 文件超过心跳阈值，需要确认 `job_id` 或通过 `external_api_tasks` 查询上游 API 状态。
3. 发现同一 `segment_anchor + stage` 有多个候选标记，需要按 `started_at / failed_at` 清理冲突。
4. 调试、审计、测试断言需要完整签名或输入输出路径。

心跳优先更新文件 mtime。只有错误信息、输入输出路径或执行详情变化时，才重写 JSON 内容。

## 7. Step Signature

`step_signature` 必须围绕 Working 文件状态计算，而不是围绕某个 Plan 文件计算。

推荐输入：

```text
sha256(
  segment_anchor,
  current_segment_id,
  dialogue_asset_keys,
  stage,
  normalized_input_paths,
  input_file_fingerprints,
  normalized_output_paths,
  storyboard_binding_fingerprint,
  relevant_prompt_template_version,
  relevant_model_config_ref
)
```

最低要求：

1. `ImagePrompt`：包含原图路径、代表 Dialogue、Prompt 模板版本、输出路径。
2. `Image_New`：包含原图或尾帧来源、ImagePrompt 路径、新图输出路径、图片模型配置。
3. `VideoPrompt`：包含 Image_New 路径、VideoPrompt 输出路径、模板版本。
4. `Video_Raw`：包含 Image_New、VideoPrompt、Raw 输出路径、视频模型配置。
5. `Video_Final_Copy`：包含 Raw 或手动视频来源、Final 输出路径、TailFrame 输出路径。
6. `Video_Final_LipSync`：包含 Raw/Final 输入视频、音频路径、Final 输出路径、对嘴型模型配置。
7. `Video_Final_AudioMix`：包含输入视频、音频路径、Final 输出路径。
8. `TailFrame`：包含对应 Final 视频路径、Final 文件指纹、TailFrame 输出路径。

Plan 重建导致 `video_generation_plan.json`、`image_generation_plan.json` 或 `video_only_generation_plan.json` 变化时，只要 Working 输入文件和 stage 语义未变，`step_signature` 应保持不变，黄 / 红状态可以继续恢复。

如果用户替换输入文件、修改 Prompt、改变 Segment anchor 或改变目标输出路径，`step_signature` 必须变化，旧状态不能再染当前槽位。

## 8. 生命周期

Plan 生成和状态标记写入必须分离：

1. 点击 Image Plan / Video Plan / Video Only Plan 生成或刷新 Plan 时，不创建 Running / Failed 文件。
2. Plan 生成只计算白 / 绿 / 灰和当前 stage 的 `step_signature_prefix`。
3. 只有用户真正点击执行某个 stage，才创建 Running 文件。
4. 只有执行失败或 stale running 被确认中断，才创建 Failed 文件。
5. 执行成功后不创建 Completed 文件；当前标记按唯一文件名移动到 `ExecutionArchive/`，绿色由业务文件和绑定自然推导。

### 8.1 开始执行

执行某个 stage 前：

1. 计算 `segment_anchor`。
2. 计算 `step_signature`。
3. 生成 `marker_uid`。
4. 将同一 `segment_anchor + stage` 下旧的 `*_Running_*.json` 和 `*_Failed_*.json` 归档后移出热路径。
5. 写入临时文件 `{anchor}_{stage}_Running_{signature12}_{marker_uid}.json.tmp`。
6. 原子改名为 `{anchor}_{stage}_Running_{signature12}_{marker_uid}.json`。
7. JSON 中写入 `marker_state=running`、`marker_uid`、`job_id`、`attempt_id`、`started_at`、`heartbeat_at`。
8. 清空旧 `error`。
9. 如果外部 API 任务还没创建，`external_api_tasks` 可以为空数组。

### 8.1.1 外部 API 任务创建后

一旦外部 API 返回任务 ID，必须立即更新当前 Running 标记：

1. 追加或更新 `external_api_tasks[]`。
2. 写入 `provider`、`api_name`、`external_task_id`、`external_request_id`、`created_at`、`last_known_status`。
3. 如果上游提供查询 URL 或结果 URL，写入 `status_url` 或 `result_urls`。
4. 更新 Running 文件 mtime。
5. 如果本地进程此后中断，恢复流程可以读取 Running 标记并用 `external_task_id` 查询上游任务，降低上游结果丢失概率。

### 8.2 心跳

长耗时 stage 必须定期更新同一个一级 Running 标记：

1. 优先通过更新 Running 文件 mtime 表示心跳，避免高频 JSON 重写。
2. 如果外部 API 查询状态变化，可以同步更新 JSON 中的 `external_api_tasks[].last_polled_at`、`last_known_status`、`result_urls`。
3. 不改变 `attempt_id`。
4. 推荐间隔 5 到 15 秒。

### 8.3 成功

业务输出发布成功后：

1. 先写入临时业务文件，校验成功后原子发布到 Working 一级标准路径。
2. 如果该 stage 需要 StoryBoard JSON 绑定，确认绑定已经写入。
3. 如果该 stage 会产生 TailFrame，必须确认 TailFrame 也已经发布到标准路径。
4. 更新当前匹配 Running 文件的 JSON，写入 `final_state=completed`、`completed_at`、最终 `output_paths`、`external_api_tasks`。
5. 将该 Running 文件原子移动到 `ExecutionArchive/Completed_{original_file_name}`。
6. 将同一 `segment_anchor + stage` 下旧的 Failed 文件也移动到 `ExecutionArchive/` 后移出热路径。
7. 不创建 Completed 标记；后续绿色由业务文件和绑定自然推导。

### 8.4 失败

失败时：

1. 如果存在当前匹配的 Running 文件，优先把它原子改名为 `{anchor}_{stage}_Failed_{signature12}_{marker_uid}.json`，并更新 JSON 内容。
2. 如果 Running 文件不存在，直接写入临时 Failed 文件后原子改名。
3. JSON 中写入 `marker_state=failed`、`failed_at`、`error.code`、`error.message`。
4. 保留 `input_paths`、`output_paths`、`step_signature`。
5. 不删除已有业务输出文件。
6. 如果已有业务输出文件和绑定比本次失败更新，红色不能覆盖绿色。
7. 当前 Failed 标记仍保留在 Working 热路径中，用于刷新后显示红色。
8. 为了保留失败发生时的历史快照，可以同时复制一份到 `ExecutionArchive/Failed_{original_file_name}`；后续重试或清理时，必须把热路径 Failed 文件移动到归档文件夹后再移除。

### 8.5 重新执行

同一 `segment_anchor + stage` 重新执行时：

1. 将同一 stage 旧的 Running / Failed 标记按唯一文件名归档，并从热路径移除。
2. 创建新的 Running 标记，文件名使用新的 `signature12` 和新的 `marker_uid`。
3. 如果重新执行成功，发布业务文件，将当前 Running / Failed 标记移动到归档文件夹后移出热路径。
4. 如果重新执行失败，写入新的 Failed 标记。

移除旧标记前必须先确认它已经移动或复制到 `ExecutionArchive/`。如果没有归档，先按唯一文件名归档，再从热路径移除。

### 8.6 删除或替换素材

用户删除或替换 StoryBoard 槽位时：

1. 清理同 `segment_anchor` 下受影响 stage 的 `*_Running_*.json` 和 `*_Failed_*.json`。
2. 删除 Final 时清理 `Video_Final*` 和 `TailFrame` 状态。
3. 删除 Raw 时清理 `Video_Raw`、`Video_Raw_TailFrame`、`Video_Final_Copy` 状态。
4. 删除 Image_New 时清理 `VideoPrompt`、`Video_Raw`、`Video_Final*`、`TailFrame` 状态。
5. 清理动作必须进入现有 edit/history 事件。
6. 清理前同样必须把被移除的 Running / Failed 标记移动到 `ExecutionArchive/`，并在 JSON 中写入 `final_state=cleared_by_asset_change`。

### 8.7 执行状态归档文件夹

`ExecutionArchive/` 是 Working 下唯一允许的状态归档文件夹。它不是素材目录，素材扫描、拼接、历史素材恢复都必须忽略它。

归档文件名：

```text
SessionOutput/storyboard/Working/ExecutionArchive/{final_state}_{original_marker_file_name}
```

示例：

```text
SessionOutput/storyboard/Working/ExecutionArchive/Completed_srt_0001_01_Video_Raw_Running_a1b2c3d4e5f6_mk7q9p2x.json
SessionOutput/storyboard/Working/ExecutionArchive/Failed_srt_0001_01_Video_Raw_Failed_a1b2c3d4e5f6_mk8r1n4c.json
SessionOutput/storyboard/Working/ExecutionArchive/ClearedByAssetChange_srt_0001_01_Video_Raw_Failed_a1b2c3d4e5f6_mk8r1n4c.json
```

归档规则：

1. 不创建大 JSON，不创建 JSONL，不合并多个执行记录。
2. 每次归档都保留一个独立状态标记 JSON 文件。
3. 归档前可以更新状态文件内部字段，例如 `archived_at`、`final_state`、`completed_at`、`failed_at`、`archive_reason`。
4. Running 成功完成时，将 Running 标记移动到归档文件夹，文件名带 `Completed`。
5. Running 失败时，热路径生成 Failed 标记；可以复制一份 Failed 快照到归档文件夹，但热路径 Failed 必须保留用于红色显示。
6. Failed 被重试、清理或被成功结果覆盖时，将热路径 Failed 文件移动到归档文件夹。
7. 归档文件必须保留 `external_api_tasks`，用于后续人工或自动补查上游 API 任务。
8. 归档不能依赖时间戳防覆盖；正常情况下由 `marker_uid` 保证文件名唯一。
9. 如果极端情况下目标归档文件已存在，必须先比较内容；内容相同可视为已归档，内容不同则追加短去重后缀，不能覆盖。
10. 如果归档移动失败，不允许删除当前 Running / Failed 标记。
11. 媒体产出文件不进入 `ExecutionArchive/`，它们继续进入现有 history。

## 9. 颜色合成规则

后端 `artifact_status.slot_states` 必须统一从 Working 文件合成，前端只消费合成结果。

合成顺序：

1. 读取 `StoryBoardSegmentAnchors.json`，确定当前 Segment 对应的 `segment_anchor`。
2. 读取 Working 一级业务文件，得到基础白 / 绿 / 灰。
3. 计算当前 stage 的 `step_signature_prefix`。
4. 用文件名匹配同层 `{anchor}_{stage}_Running_{signature12}_{marker_uid}.json` 或 `{anchor}_{stage}_Failed_{signature12}_{marker_uid}.json`。
5. 如果业务文件存在、内容有效、StoryBoard JSON 绑定一致，并且没有当前匹配 Running 标记正在覆盖它，显示绿色。
6. 如果存在当前匹配 Running 标记，且本次 attempt 的目标业务文件尚未完成，显示黄色。
7. 如果存在当前匹配 Failed 标记，且目标业务文件不存在，或失败 attempt 晚于当前业务文件绑定，显示红色。
8. 如果只有不匹配当前 signature 的旧 Running / Failed 标记，忽略，不读取 JSON。
9. 没有匹配状态标记时，保持基础白 / 绿 / 灰。

这保证：

1. 刷新页面后黄 / 红恢复。
2. 另一个浏览器 Session 看到同一状态。
3. Image Plan、Video Plan、Video Only Plan 的同一 stage 使用同一事实源。
4. 已落盘并绑定的业务文件优先绿色。
5. 旧失败不会污染新输入、新 Segment 或新 Prompt。

## 10. TailFrame 一致性

TailFrame 是终视频的下游消费凭证，必须跟随所有可能生成终视频的路径：

1. 新视频生成 Raw 后，可生成 `{anchor}_Video_Raw_TailFrame.png` 作为诊断尾帧，但它不能替代终视频尾帧。
2. 用户把 Raw 或手动视频拷贝为 Final 后，必须生成 `{anchor}_TailFrame.png`。
3. 对嘴型生成 Final 后，必须生成 `{anchor}_TailFrame.png`。
4. 声音合成或音频替换生成 Final 后，必须生成 `{anchor}_TailFrame.png`。
5. 如果 Final 已经存在但 TailFrame 缺失，`TailFrame` stage 应显示白色可执行或红色失败，而不是把下游误判为可继承。
6. 跨 Segment 继承只允许继承上游 Final 对应的 `{anchor}_TailFrame.png`，不能继承 Raw 诊断尾帧，也不能从空镜错误继承到需要人物延续的 Segment。

## 11. Stale Running 处理

运行状态可能因为后端重启、进程崩溃或浏览器中断遗留。必须有过期规则：

1. 如果 `*_Running_*.json` 的 mtime 超过配置阈值，例如 10 分钟，后端再读取 JSON 检查 `job_id` 是否仍在运行。
2. 如果本地 job 不存在，但 `external_api_tasks` 里有外部任务 ID，先查询上游 API。
3. 如果上游任务仍在运行，更新 Running 标记 mtime 和 `external_api_tasks[].last_known_status`，继续显示黄色。
4. 如果上游任务成功且可获取结果，尝试下载或绑定输出文件；成功发布后按成功生命周期归档并移除 Running。
5. 如果上游任务失败、过期、取消，或无法查询，将该 Running 文件原子改名为对应 Failed 文件。
6. `error.code=stale_running_status` 或上游 API 对应错误码。
7. UI 显示红色，并提示“上次执行中断”或上游失败摘要。

## 12. 与现有 Execution JSON 的关系

现有 execution JSON 可以继续保留：

```text
SessionOutput/storyboard/video_plan_execution_state.json
SessionOutput/storyboard/video_plan_execution_result.json
SessionOutput/storyboard/image_plan_execution_state.json
SessionOutput/storyboard/image_plan_execution_result.json
SessionOutput/storyboard/video_only_plan_execution_state.json
SessionOutput/storyboard/video_only_plan_execution_result.json
```

分工：

1. `*_execution_state.json`：整次执行 job 的摘要、进度、审计状态。
2. `*_execution_result.json`：工具结果和执行报告。
3. `Working/{anchor}_{stage}_{Running/Failed}_{signature12}_{marker_uid}.json`：槽位颜色事实源。

前端槽位颜色不得依赖三套 execution JSON 自行染色；它们只用于展示执行摘要和错误详情。

## 13. 后端实现建议

新增统一服务模块：

```text
backend/opcrew_backend/koubo/koubo_storyboard/working_status_services.py
```

职责：

1. `resolve_segment_anchor(segment, storyboard, anchors_file)`。
2. `marker_paths(working_dir, segment_anchor, stage, signature_prefix)`。
3. `business_path(working_dir, segment_anchor, stage)`。
4. `compute_stage_signature(stage, segment_anchor, inputs, storyboard, settings)`。
5. `write_running_marker(...)`。
6. `archive_and_remove_success_markers(...)`。
7. `write_failed_marker(...)`。
8. `clear_markers_for_asset_change(segment_anchor, changed_stage)`。
9. `scan_matching_marker_names(segment_anchor, stage, signature_prefix)`。
10. `read_marker_detail(marker_path)`。
11. `merge_marker_into_slot_state(base_slot, marker_name, business_output_status)`。
12. `mark_stale_running_markers(...)`。
13. `archive_marker_file(...)`。
14. `ensure_marker_file_archived_before_remove(...)`。
15. `update_external_api_task(...)`。
16. `recover_running_marker_from_external_task(...)`。

现有入口都应调用同一服务：

1. Video Plan artifact status。
2. Image Plan artifact status。
3. Video Only Plan artifact status。
4. StoryBoard 槽位状态服务。
5. 删除 / 替换素材的 stale edit 清理服务。

## 14. 工具实现建议

各执行器在 stage 边界写统一 Working 状态标记文件：

1. `05_02_VideoPlanExecutor.py`
   - `Audio_Final`
   - `SegmentAudio_Final`
   - `ImagePrompt`
   - `Image_New`
   - `VideoPrompt`
   - `Video_Raw`
   - `Video_Final_LipSync`
   - `Video_Final_AudioMix`
   - `Video_Final`
   - `TailFrame`
2. `05_04_ImagePlanExecutor.py`
   - `ImagePrompt`
   - `Image_New`
3. `05_06_VideoOnlyPlanExecutor.py`
   - `VideoPrompt`
   - `Video_Raw`
   - `Video_Raw_TailFrame`
4. VideoOnlyPlan confirm-final API
   - `Video_Final_Copy`
   - `Video_Final`
   - `TailFrame`

执行入口可以记录 `source_view`，但不能把状态按入口隔离。

## 15. 前端实现建议

前端应简化为显示后端合成结果：

1. Modal 和 StoryBoard 列表都读后端 `artifact_status.slot_states`。
2. 不再按 ImagePlan / VideoPlan / VideoOnlyPlan 分别合并 execution state 染色。
3. `execution_state` 只用于顶部进度、当前 job、错误详情。
4. 刷新页面和另一个浏览器 Session 只要重新请求 payload，就能显示同一黄 / 红。

## 16. 测试设计原则

现有测试已经覆盖大量业务状态：

1. `test_koubo_storyboard_slot_state_contract.py`：基础槽位颜色、运行 / 失败不覆盖已落盘文件。
2. `test_koubo_non_single_scene_plan_state_contract.py`：非单 Scene 的 ImagePlan / VideoOnlyPlan / VideoPlan 关键颜色、跨 Shot 尾帧、空镜尾帧、Raw/Final 语义。
3. `test_analysis_v1_image_plan_tools_contract.py`：ImagePlan 执行器写 execution state 和失败状态。
4. `test_analysis_v1_video_only_plan_tools_contract.py`：VideoOnlyPlan 执行器、Raw/Final、TailFrame。
5. `test_analysis_v1_video_plan_executor_resilience_contract.py`：05_02 执行失败、对嘴失败、音频/图片/视频发布边界。
6. `test_koubo_storyboard_stale_edit_contract.py`：删除素材时清理下游执行状态。
7. `test_koubo_storyboard_composer_scope_contract.py`：stale running 状态自动失败的参考模式。

新增测试不重复大矩阵，聚焦“统一 Working 一级状态标记文件”和“文件名热路径判断”。

## 17. 最小但安全的新增测试案例

### T1 Working 一级状态标记不进入素材池

目标：保证 `*_Running_*.json` 和 `*_Failed_*.json` 是状态标记，不被当成素材。

步骤：

1. 在 Working 写入 `srt_0001_01_Image_New_Running_a1b2c3d4e5f6_mk7q9p2x.json`。
2. 调用素材扫描 / artifact status。
3. 确认它不进入图片、视频、音频素材列表。
4. 确认状态服务可通过文件名识别它。

覆盖风险：状态标记文件污染素材池或拼接输入。

### T2 三个 Plan 读取同一 Running 状态

目标：证明状态不按 Plan 隔离。

步骤：

1. 写入 `srt_0001_01_Video_Raw_Running_a1b2c3d4e5f6_mk7q9p2x.json`。
2. 分别调用 Video Plan、Video Only Plan、StoryBoard 槽位状态 payload。
3. 三个入口都应把同一 Raw stage 显示为黄色。

覆盖风险：三个 Plan 显示不一致。

### T3 Failed 状态跨刷新恢复红色

目标：失败状态来自 Working 文件。

步骤：

1. 写入 `srt_0001_01_Image_New_Failed_a1b2c3d4e5f6_mk7q9p2x.json`。
2. 不提供 `srt_0001_01_Image_New.png`。
3. 重新生成 Image Plan payload。
4. 期望 Image_New 红色，错误可读。

覆盖风险：刷新后红色退回白色。

### T4 业务文件优先绿色

目标：旧失败不覆盖已完成业务文件。

步骤：

1. 写入 `srt_0001_01_Image_New_Failed_a1b2c3d4e5f6_mk7q9p2x.json`。
2. 写入对应业务文件，例如 `srt_0001_01_Image_New.png`。
3. 确保 StoryBoard JSON 绑定一致。
4. 重新调用状态合成。
5. 期望绿色。

覆盖风险：历史失败覆盖成功结果。

### T5 重新运行覆盖旧失败

目标：重试后旧红消失。

步骤：

1. 写入 `srt_0001_01_Video_Raw_Failed_a1b2c3d4e5f6_mk7q9p2x.json`，attempt_id=A。
2. 触发重新执行，服务归档旧 Failed 并写入 `srt_0001_01_Video_Raw_Running_b2c3d4e5f6a1_mk8r1n4c.json`，attempt_id=B。
3. 扫描 Working 文件名。
4. 期望只存在新的 Running 标记，旧 Failed 已删除。
5. UI 合成黄色。

覆盖风险：用户重试后仍显示旧红。

### T6 Step Signature 变化时忽略旧状态

目标：输入变化不被旧状态污染。

步骤：

1. 写入 `srt_0001_01_Video_Raw_Failed_oldsig000001_mk7q9p2x.json`。
2. 替换 Image_New 或修改 VideoPrompt，使当前 signature=new。
3. 重新调用状态合成。
4. 期望旧状态忽略，按文件条件显示白 / 灰 / 绿。

覆盖风险：旧失败污染新 Prompt、新素材、新 Segment。

### T7 Plan 文件变化但 Working 输入不变时保留状态

目标：不依赖完整 Plan cache。

步骤：

1. 写入 `srt_0001_01_Video_Raw_Running_a1b2c3d4e5f6_mk7q9p2x.json`。
2. 重新生成 Plan，使 plan 文件时间或 plan hash 变化。
3. 保持 Working 输入文件不变，因此当前 `signature12` 仍为 `a1b2c3d4e5f6`。
4. 重新调用状态合成。
5. 期望仍为黄色。

覆盖风险：每次点击 Plan 重建后黄 / 红丢失。

### T8 Segment Anchor 映射稳定

目标：Segment 调整后还能匹配旧 Working 文件。

步骤：

1. 建立 `StoryBoardSegmentAnchors.json`，`srt_0001_01` 绑定旧 segment。
2. 重建 Plan，使当前 segment_id 变化但首个代表 Dialogue 仍是 `srt_0001_01`。
3. 写入或读取 `srt_0001_01_Video_Raw_Running_a1b2c3d4e5f6_mk7q9p2x.json`。
4. 期望新 Segment 仍匹配旧 anchor。

覆盖风险：Segment 重建导致文件和状态失联。

### T9 Segment 拆分时 Anchor 不串线

目标：拆分后旧状态只跟随代表 Dialogue。

步骤：

1. 原 Segment 包含 `srt_0001_01`、`srt_0001_02`，anchor 为 `srt_0001_01`。
2. 拆分成两个 Segment。
3. 包含 `srt_0001_01` 的 Segment 继承旧状态。
4. 包含 `srt_0001_02` 的 Segment 使用新 anchor，不继承旧黄 / 红。

覆盖风险：拆分后状态错误串到另一个 Segment。

### T10 Stale Running 自动转失败

目标：在没有外部任务可恢复时，避免永久黄色。

步骤：

1. 写入 Running 标记文件，并把 mtime 调整为超过阈值。
2. 模拟 job 不存在，且没有可查询的 `external_api_tasks`。
3. 调用 stale 状态扫描。
4. 期望 Running 文件被原子改名为 Failed 文件，JSON 中 `error.code=stale_running_status`。
5. UI 合成红色。

覆盖风险：后端重启或崩溃后永久黄色。

### T11 删除素材清理下游状态

目标：删除业务文件后，旧状态不污染下游。

步骤：

1. 写入 `Image_New`、`VideoPrompt`、`Video_Raw`、`Video_Final`、`TailFrame` 状态。
2. 删除 `Image_New` 并保存 StoryBoard。
3. 确认下游 Running / Failed 标记被清理。
4. 重新生成 VideoOnlyPlan，Raw 不因旧状态显示黄 / 红。

覆盖风险：删除素材后旧状态继续污染。

### T12 TailFrame 多路径一致性

目标：所有 Final 生成路径都产出同一标准终视频尾帧。

步骤：

1. 分别模拟 Raw 拷贝 Final、对嘴型 Final、声音合成 Final。
2. 每条路径完成后都检查 `{anchor}_Video_Final.mp4` 和 `{anchor}_TailFrame.png`。
3. 三条路径都应让 `TailFrame` stage 绿色。
4. 如果 Final 存在但 TailFrame 缺失，TailFrame 不得显示绿色。

覆盖风险：下游消费拿不到尾帧。

### T13 Plan 生成不创建运行状态

目标：避免单纯打开或刷新 Plan 就制造黄 / 红状态。

步骤：

1. 清空 Working 下当前 anchor 的 Running / Failed 标记。
2. 点击或调用 Image Plan / Video Plan / Video Only Plan 生成 payload。
3. 不触发任何执行动作。
4. 期望 Working 下不新增 `*_Running_*.json` 或 `*_Failed_*.json`。

覆盖风险：Plan 刷新误写状态，导致多 Session 看到假的运行中。

### T14 成功后归档并移出热路径

目标：完成态回归业务文件事实，同时保留执行追溯。

步骤：

1. 写入 `srt_0001_01_Video_Raw_Running_a1b2c3d4e5f6_mk7q9p2x.json`。
2. 发布 `srt_0001_01_Video_Raw.mp4`。
3. 执行成功收尾。
4. 期望 Running 标记被移动到 `ExecutionArchive/Completed_{original_file_name}`，不要求额外时间戳。
5. 期望 Running / Failed 标记移出热路径，也不存在 Completed 标记。
6. 状态合成从业务文件显示绿色。

覆盖风险：Completed 文件成为第四套事实源，或成功执行记录丢失不可追溯。

### T15 热路径不依赖 JSON 内容读取

目标：保证颜色判断主要基于文件名和业务文件。

步骤：

1. 写入文件名匹配的 Running 标记，但 JSON 内容只保留最小合法字段。
2. 调用状态合成。
3. 期望不需要读取完整详情即可显示黄色。
4. 当请求错误详情或处理 stale running 时，才读取 JSON。

覆盖风险：大量列表刷新时频繁解析所有状态 JSON。

### T16 Running 标记保存外部 API 任务 ID

目标：本地进程中断后仍能追查上游任务。

步骤：

1. 创建 Running 标记，初始 `external_api_tasks=[]`。
2. 模拟外部 API 返回 `external_task_id=wan_task_abc123`。
3. 更新 Running 标记 JSON。
4. 重新读取 Running 标记。
5. 期望 `external_api_tasks[0].external_task_id`、`provider`、`api_name`、`last_known_status` 都存在。

覆盖风险：上游任务已创建，但本地状态丢失，导致生成结果无法找回。

### T17 Stale Running 先查外部 API 再转失败

目标：减少上游已成功但本地误判失败的情况。

步骤：

1. 写入 Running 标记，mtime 超时，并包含 `external_task_id`。
2. 模拟本地 job 不存在。
3. 模拟上游 API 查询返回 succeeded 和结果 URL。
4. 调用 stale running 处理。
5. 期望系统尝试下载 / 发布输出文件，成功后归档 completed，并移除 Running。
6. 只有上游返回 failed / canceled / not_found 时，才转 Failed。

覆盖风险：本地进程丢失后，误把可恢复的上游成功任务标红。

### T18 Failed 与清理都必须进入归档

目标：失败记录和被清理记录不丢。

步骤：

1. 写入 Running 标记并触发失败。
2. 期望 Failed 标记留在热路径，同时 `ExecutionArchive/` 中存在一份独立 Failed 快照。
3. 触发重新执行或删除素材清理。
4. 清理旧 Failed 前再次确认对应状态文件已经移动到 `ExecutionArchive/`。

覆盖风险：红色状态可见但历史执行详情被覆盖或删除。

## 18. 建议测试文件落点

新增：

```text
backend/tests/contracts/test_koubo_storyboard_working_status_contract.py
```

建议第一批覆盖 T1 到 T10、T13 到 T18。T11 和 T12 与删除清理、尾帧执行器更相关，可以在接入工具时同步补齐，但不能晚于前端开始依赖该状态。

扩展现有测试：

1. `test_koubo_storyboard_slot_state_contract.py`
   - 增加 `*_Running_*.json` / `*_Failed_*.json` 合成黄 / 红优先级表。
   - 增加 Running 标记携带 `external_api_tasks` 后仍不影响热路径颜色合成。
2. `test_koubo_non_single_scene_plan_state_contract.py`
   - 增加 Segment anchor 重建、跨 Shot 尾帧继承与状态标记文件匹配。
3. `test_koubo_storyboard_stale_edit_contract.py`
   - 增加删除 Image_New / Raw / Final 时清理下游状态。
4. `test_analysis_v1_image_plan_tools_contract.py`
   - 增加 05_04 写 `ImagePrompt_Running/Failed`、`Image_New_Running/Failed` 的断言。
5. `test_analysis_v1_video_only_plan_tools_contract.py`
   - 增加 05_06 写 `VideoPrompt_Running/Failed`、`Video_Raw_Running/Failed`、`Video_Raw_TailFrame_Running/Failed` 的断言。
6. `test_analysis_v1_video_plan_executor_resilience_contract.py`
   - 增加 05_02 写 `Video_Final_*_Running/Failed`、`TailFrame_Running/Failed` 的断言。
   - 增加外部 API 任务 ID 写入、stale running 查询上游后恢复输出的断言。

## 19. 验收标准

1. Working 当前热路径下的状态标记都是一级 `*_Running_*.json` 或 `*_Failed_*.json`；历史状态只允许进入 `ExecutionArchive/`。
2. Image Plan、Video Plan、Video Only Plan 对同一 stage 的黄 / 红显示一致。
3. 刷新页面后，运行中仍显示黄色。
4. 刷新页面后，失败仍显示红色。
5. 另一个浏览器 Session 打开同一任务，也能看到同一黄 / 红。
6. 重新运行同一 stage 后，旧失败不再显示。
7. 业务文件成功落盘并绑定后，旧运行 / 失败状态不覆盖绿色。
8. Plan 重建但 Working 输入未变时，黄 / 红保留。
9. Working 输入、Prompt 或 Segment anchor 改变时，旧黄 / 红忽略。
10. Segment 拆分 / 合并后，状态只跟随稳定代表 Dialogue，不串到其他 Segment。
11. 后端重启后的 stale running 不会永久黄色。
12. 所有生成 Final 的路径都生成标准 `{anchor}_TailFrame.png`。
13. 单纯生成或刷新 Plan 不创建 Running / Failed 标记。
14. 执行成功后先归档，再移除 Running / Failed 标记，不创建 Completed 标记。
15. 常规颜色合成不需要解析所有状态 JSON。
16. Running 标记必须保存外部 API 任务 ID，并可用于恢复查询。
17. stale running 必须先尝试用外部 API 任务 ID 查询上游，再决定补拉结果或转 Failed。
18. Completed、Failed、cleared_by_asset_change 都必须以独立状态文件进入 `ExecutionArchive/`，不合并成大 JSON。
