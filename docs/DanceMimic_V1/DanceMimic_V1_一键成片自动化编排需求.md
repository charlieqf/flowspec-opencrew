# DanceMimic_V1 一键成片自动化编排需求

版本：v0.1
状态：需求确认稿
适用范围：DanceMimic_V1 任务页、标准 StoryBoard 后续成片链路、未来可复用的一键成片编排

## 1. 背景

DanceMimic_V1 当前已经完成 00-03 的参考视频拆解与标准 StoryBoard 构建：

```text
00 准备运行参数
01 拆解参考视频
02 处理人脸合规
03 构建故事版
```

03 完成后，任务已经可以进入 StoryBoard 页面继续执行 VideoPlan 和 Composer。用户现在需要在动作模拟任务页上增加一个独立入口，把 StoryBoard 后续成片链路自动串起来，减少手动进入 StoryBoard、打开生成计划、执行计划、再执行合成的操作成本。

该能力命名为：

```text
一键成片
```

它不是替代 00-03 的重新运行或强制重建，而是在 00-03 生成的 StoryBoard 基础上继续执行完整视频生成与合成链路。

## 2. 产品目标

一键成片的目标是把已经拆好的 StoryBoard 自动跑成完整视频：

```text
已完成 StoryBoard
-> 05-01 生成 / 强制刷新 VideoPlan
-> 05-02 执行 VideoPlan
-> 等待每个 Segment 的 Final Video 与 TailFrame 完成
-> 06-01 / Composer 合并成最终视频
-> 返回最终成片产物
```

用户只需要点击一次按钮，即可看到完整执行进度和最终成片结果。

## 3. UI 入口需求

### 3.1 按钮位置

在 DanceMimic_V1 任务详情页顶部操作区新增按钮：

```text
任务列表 | 重新运行 | 一键成片 | 强制重建 | 故事板
```

按钮位置必须位于：

```text
重新运行 和 强制重建 中间
```

### 3.2 按钮文案

按钮文案：

```text
一键成片
```

建议 title / aria-label：

```text
一键生成完整视频并合成
```

### 3.3 与现有按钮的职责边界

| 按钮 | 职责 |
| --- | --- |
| 重新运行 | 重新执行 DanceMimic 00-03，刷新 StoryBoard 前置产物 |
| 一键成片 | 基于已存在 StoryBoard，执行 05-01、05-02、Composer |
| 强制重建 | 强制重跑 DanceMimic 00-03，并处理下游 stale 状态 |
| 故事板 | 打开 StoryBoard 编辑页面 |

一键成片不得隐式重跑 00-03，除非后续产品明确增加“先重建再成片”的复合模式。

## 4. 弹窗需求

### 4.1 弹窗形态

一键成片点击后打开运行弹窗，视觉和交互应与当前“重新运行”弹窗保持一致：

1. 使用相同的居中弹窗样式。
2. 支持拖动。
3. 显示任务 ID、会话 ID、尝试 ID。
4. 保留关闭按钮。
5. 显示总耗时。
6. 支持运行中轮询刷新。

### 4.2 顶层步骤

弹窗需要显示一键成片的顶层阶段：

| 阶段 | 文案 | 说明 |
| --- | --- | --- |
| 05-01 | 生成视频计划 | 强制刷新 VideoPlan，默认整条任务范围 |
| 05-02 | 执行视频计划 | 逐 Segment 生成 Raw / Final / TailFrame |
| 06-01 | 合并最终视频 | Composer 合并完整成片 |

### 4.3 Segment 级状态

05-02 阶段必须展开显示每句话 / 每个 Segment 的状态。

每个 Segment 至少显示：

| 状态项 | 含义 |
| --- | --- |
| Audio | 当前 Dialogue / Segment 音频是否存在或已合成到 SegmentAudio |
| 首帧 / 新图 | 当前 Segment 的首帧来源是否就绪 |
| Raw Video | 视频模型生成的原始视频是否完成 |
| 音频合成 / Final Video | 是否完成 Raw Video + SegmentAudio 的最终视频合成 |
| TailFrame | 是否已从当前 Final Video 提取尾帧 |

### 4.4 TailFrame 链路展示

DanceMimic 的多段生成必须明确展示尾帧续接关系：

```text
S1 Final Video -> S1 TailFrame
S1 TailFrame -> S2 Image_New / 首帧
S2 Final Video -> S2 TailFrame
S2 TailFrame -> S3 Image_New / 首帧
...
```

弹窗中应让用户能看出：

1. 第 1 段是起点，不依赖上一段尾帧。
2. 第 2 段开始，首帧来自上一段 TailFrame。
3. 当前段 TailFrame 是下一段首帧的依赖。
4. 任意 TailFrame 缺失时，后续段应显示等待或阻断原因。

### 4.5 最终成片展示

Composer 完成后，弹窗应显示最终产物：

1. 最终视频路径。
2. 最终视频是否存在。
3. 合成范围：task / shot / scene。
4. 成片状态：完成、失败、阻断。
5. 允许用户打开 StoryBoard 或查看最终视频。

## 5. 默认执行策略

### 5.1 05-01 默认强制刷新

一键成片默认必须强制刷新 VideoPlan：

```json
{
  "force": true
}
```

原因：

1. 避免复用旧 cache plan。
2. 避免 StoryBoard 已变化但计划仍命中缓存。
3. 避免后续 Composer 使用局部或过期 VideoPlan。

### 5.2 默认整条任务范围

一键成片默认使用整条任务范围：

```json
{
  "target": {
    "target_type": "task",
    "shot_id": "",
    "scene_id": ""
  }
}
```

不得默认跟随当前选中的 Dialogue、Scene 或 Shot。

原因：

1. 一键成片的语义是完整成片。
2. 当前 Task #24 只有一个 shot / scene，scene 范围看起来等同整条，但未来多 shot / 多 scene 时必须保持整片语义。
3. Composer 对 VideoPlan 覆盖范围有校验，局部 VideoPlan 无法合成完整视频。

### 5.3 执行顺序

一键成片必须串行执行：

```text
05-01 完成后才能启动 05-02
05-02 完成后才能启动 Composer
Composer 完成后才显示最终成片完成
```

不得只发起请求后立即进入下一步，必须根据执行状态轮询确认阶段完成。

### 5.4 错误处理

任一阶段失败或阻断时：

1. 停止后续阶段。
2. 弹窗保留已完成阶段状态。
3. 显示失败阶段、失败 Segment、失败步骤和错误原因。
4. 不自动重跑 00-03。
5. 允许用户关闭弹窗后进入 StoryBoard 手动修复。

## 6. DanceMimic 专用行为

### 6.1 不执行对嘴型

DanceMimic_V1 一键成片必须明确保证：不执行对嘴型。

DanceMimic_V1 的 05-01 规划中，DanceMimic reference video Segment 必须走非对嘴型路径：

```json
{
  "need_lipsync": false,
  "need_audio_video_sync": true,
  "sync_mode": "audio_replace_retime",
  "lipsync_reason": "dance_mimic_reference_video"
}
```

因此一键成片执行 05-02 时：

1. 不调用 lipsync provider。
2. 生成 Raw Video 后，用 SegmentAudio 与 Raw Video 做音频合成 / 重计时。
3. 最终输出 Final Video。

禁止行为：

1. 不得把 DanceMimic Segment 自动切回 `sync_mode=lipsync`。
2. 不得因为画面里有人脸、人物或音频存在而触发对嘴型。
3. 不得在一键成片中调用 lipsync provider 作为兜底。
4. 不得把“音频合成”解释为“音频匹配 / 对嘴型同步”；这里的语义是 `audio_replace_retime`。

### 6.2 尾帧续接

DanceMimic_V1 一键成片必须明确保证：从 Segment 2 开始，上一段最终视频的尾帧必须成为下一段的新图 / 首帧。

DanceMimic_V1 的连续多段计划必须遵循：

| Segment | 首帧来源 |
| --- | --- |
| Segment 1 | 初始目标身份图或已有起始视频 |
| Segment 2 | Segment 1 的 TailFrame |
| Segment 3 | Segment 2 的 TailFrame |
| Segment N | Segment N-1 的 TailFrame |

VideoPlan 中应体现为：

```json
{
  "first_frame": {
    "source_type": "previous_segment_tail_frame",
    "materialize_first_frame": {
      "required": true,
      "copy_from_path": "SessionOutput/storyboard/Working/{prev}_TailFrame.png",
      "copy_to_path": "SessionOutput/storyboard/Working/{current}_Image_New.png"
    }
  }
}
```

执行器必须在当前 Segment 生成视频前物化该首帧。

硬性要求：

1. Segment 2+ 的 `first_frame.source_type` 必须是 `previous_segment_tail_frame`，除非上游明确没有上一段。
2. Segment 2+ 必须先把上一段 TailFrame 复制 / 物化到当前 `{dialogue_asset_key}_Image_New.*`，再生成当前 Raw Video。
3. 当前 Segment 的 TailFrame 生成完成后，才允许后续 Segment 继续消费它。
4. 如果上一段 TailFrame 缺失，后续 Segment 必须等待或 blocked，不得回退到原图生成新图。
5. 该 TailFrame 链路必须在弹窗中可见，不能只显示一个笼统的“执行中”。

### 6.3 原图不干扰首帧

DanceMimic_V1 一键成片必须明确保证：不需要、也不允许通过原图重新生成新图来替代尾帧首帧链路。

DanceMimic 的原图 / source image 用于身份参考和 UI 展示，不应覆盖 05-01 已规划的 TailFrame 首帧链路。

只要 VideoPlan 中当前 Segment 的 `first_frame.source_type` 是：

```text
previous_segment_tail_frame
```

则 05-02 必须以上一段 TailFrame 物化后的 `Image_New` 作为当前首帧，不得回退到 `image_path` 或 `source_image_paths[]` 重新生成新图。

禁止行为：

1. 不得因为 `image_path` / `source_image_paths[]` 存在，就从原图生成新的 `Image_New`。
2. 不得把 UI 上展示的“原图”当成 Segment 2+ 的视频首帧输入。
3. 不得在尾帧存在或可等待生成时，启动图片模型生成新图。
4. 不得用原图生成的新图覆盖由上一段 TailFrame 物化出来的 `Image_New`。
5. 原图可以作为身份参考字段保留，但不能改变 `previous_segment_tail_frame -> Image_New -> Raw Video` 的执行链路。

## 7. 通用 StoryBoard 可复用目标

一键成片不应只为 DanceMimic 写死。后续多个标准 StoryBoard 都会需要类似能力：

```text
标准 StoryBoard 编辑完成
-> 自动生成计划
-> 自动执行计划
-> 自动合成成片
```

因此实现时应尽量抽象为通用编排能力，DanceMimic 只是第一个接入对象。

### 7.1 通用编排输入

通用一键成片至少需要：

| 输入 | 说明 |
| --- | --- |
| task_id | 当前 StoryBoard 对应任务 |
| target | 默认 task/all，可扩展 shot / scene |
| force_video_plan | 是否强制刷新 05-01 |
| composer_settings | 合成参数 |
| source_workflow | dance_mimic_v1 / analysis_v1 / script_storyboard 等 |

### 7.2 通用阶段模型

建议抽象阶段：

```text
plan_video
execute_video_plan
compose_video
```

阶段内部保留 Segment / Step 状态，不把所有状态压成一个简单进度条。

### 7.3 分阶段落地原则

当前产品路线应先稳定“拆开的 StoryBoard 操作”：

1. StoryBoard 编辑。
2. VideoPlan 生成。
3. VideoPlan 执行。
4. Composer 合成。
5. 每个弹窗的状态展示、失败原因和产物绑定。

当这些拆开操作稳定后，再把它们接成“一键成片”自动化链路。

一键成片本质是编排器，不应绕过已稳定的单步能力，也不应复制 05-01、05-02、Composer 内部逻辑。

## 8. 后端接口建议

### 8.1 新增一键成片接口

建议新增：

```text
POST /api/dance-mimic-v1/tasks/{task_id}/one-click-film
GET  /api/dance-mimic-v1/tasks/{task_id}/one-click-film/{job_id}
```

也可以在通用 StoryBoard 路由下实现：

```text
POST /api/koubo-storyboard/tasks/{task_id}/one-click-film
GET  /api/koubo-storyboard/tasks/{task_id}/one-click-film/{job_id}
```

若目标是未来多 StoryBoard 复用，优先考虑通用 StoryBoard 路由；DanceMimic 页面只调用该通用接口。

### 8.2 POST payload

```json
{
  "target": {
    "target_type": "task",
    "shot_id": "",
    "scene_id": ""
  },
  "force_video_plan": true,
  "composer_settings": {
    "subtitle_mode": "hyperframe",
    "watermark_mode": "always",
    "force": true
  },
  "action_source": "dance_mimic_one_click_film"
}
```

### 8.3 状态 payload

接口返回应包含：

```json
{
  "job_id": "one_click_film_...",
  "status": "running",
  "task_id": 24,
  "session_id": 25,
  "current_stage": "execute_video_plan",
  "stages": [
    {
      "id": "05_01",
      "label": "生成视频计划",
      "status": "completed"
    },
    {
      "id": "05_02",
      "label": "执行视频计划",
      "status": "running"
    },
    {
      "id": "06_01",
      "label": "合并最终视频",
      "status": "pending"
    }
  ],
  "segments": [
    {
      "asset_key": "dak_0002",
      "label": "Dance motion segment 0002",
      "steps": {
        "audio": "completed",
        "first_frame": "completed",
        "raw_video": "running",
        "final_video": "pending",
        "tail_frame": "pending"
      },
      "first_frame": {
        "source_type": "previous_segment_tail_frame",
        "copy_from_path": "SessionOutput/storyboard/Working/dak_0001_TailFrame.png",
        "copy_to_path": "SessionOutput/storyboard/Working/dak_0002_Image_New.png"
      }
    }
  ],
  "composer": {
    "status": "pending",
    "output_path": ""
  }
}
```

### 8.4 状态来源

一键成片状态不得重新发明状态体系，应汇总现有来源：

| 状态 | 来源 |
| --- | --- |
| 05-01 阶段 | `video_generation_plan.json`、`video_generation_plan.ui_cache.json`、VideoPlan 生成接口返回 |
| 05-02 阶段 | `video_plan_execution_state.json`、`video_plan_execution_result.json`、artifact_status |
| Segment 槽位 | VideoPlan `artifact_status.slot_states`、执行状态 tracker |
| TailFrame | `segment.tail_frame.planned_path` 文件存在性与执行状态 |
| Composer | `video_plan_compose_state.json`、`video_plan_compose_result.json` |

## 9. 前端实现建议

### 9.1 DanceMimic 页面入口

在 `DanceMimicV1Module` 顶部操作区增加按钮：

```text
一键成片
```

按钮 disabled 条件：

1. 未加载 task。
2. 00-03 尚未完成。
3. StoryBoard 不存在。
4. 当前已有一键成片 / 05-02 / Composer 正在运行。
5. 下游 stale 状态阻断，需要先重跑 05-01 或 00-03。

### 9.2 复用运行弹窗

弹窗可复用当前共享 `RunProgressDialog` 的外壳，但内部需要新增一键成片专用内容：

1. 顶层 stage 列表。
2. Segment 状态表。
3. TailFrame 依赖展示。
4. Composer 结果区。

不能只显示 05-01 / 05-02 / 06-01 三行，否则用户无法判断是哪句话、哪个 TailFrame、哪个 Final Video 失败。

### 9.3 状态文案

推荐状态文案：

| 机器状态 | UI 文案 |
| --- | --- |
| pending | 等待 |
| running | 运行中 |
| completed | 完成 |
| blocked | 阻断 |
| failed | 失败 |
| skipped | 跳过 |
| copied | 已物化 |

TailFrame 相关文案：

| 场景 | UI 文案 |
| --- | --- |
| 当前段尾帧已生成 | 尾帧已生成 |
| 下一段等待上一段尾帧 | 等待上一段尾帧 |
| 上一段尾帧已复制为当前新图 | 尾帧已作为首帧 |
| 上一段尾帧缺失 | 缺少上一段尾帧 |

## 10. 执行互斥

一键成片运行时必须与以下操作互斥：

1. DanceMimic 重新运行。
2. DanceMimic 强制重建。
3. StoryBoard 保存。
4. VideoPlan 生成。
5. VideoPlan 执行。
6. ImagePlan 执行。
7. VideoOnlyPlan 执行。
8. Composer 执行。
9. 口播 / 空镜标记。
10. Dialogue 结构编辑、合并、删除。

原因：这些操作都会改变 StoryBoard、Working 文件、plan hash、执行状态或最终产物绑定。

## 11. 验收标准

### 11.1 DanceMimic Task #24 类场景

以 1 Shot / 1 Scene / 5 Segment 的 DanceMimic 任务为例：

1. 点击“一键成片”后，05-01 以 `target_type=task` 和 `force=true` 生成 VideoPlan。
2. VideoPlan 中 5 个 Segment 均为 `sync_mode=audio_replace_retime`，且 `need_lipsync=false`。
3. Segment 2-5 的 `first_frame.source_type` 为 `previous_segment_tail_frame`。
4. 05-02 执行时，Segment 2-5 在生成 Raw Video 前先物化上一段 TailFrame 为当前 `Image_New`。
5. 每个 Segment 完成后生成 Final Video 和 TailFrame。
6. Composer 在 05-02 完成后自动执行。
7. Composer 输出完整成片。
8. 弹窗显示每个 Segment 的 Audio、首帧、Raw Video、Final Video、TailFrame 状态。
9. 任意 Segment 失败时，Composer 不启动，并显示失败 Segment 与失败步骤。
10. 全流程不得调用 lipsync provider。
11. Segment 2-5 不得从 `image_path`、`source_image_paths[]` 或 UI 原图生成新的首帧图。
12. 如果 Segment 2-5 的上一段 TailFrame 缺失，必须等待或 blocked，不得回退到原图生成新图。
13. Segment 2-5 的当前 `Image_New` 必须来自上一段 TailFrame 的复制 / 物化。

### 11.2 通用 StoryBoard 场景

对于普通 StoryBoard：

1. 一键成片默认覆盖整条任务。
2. 05-01 / 05-02 / Composer 仍使用现有通用逻辑。
3. 对嘴型、空镜、绑定视频、TailFrame、原图、新图等规则由 VideoPlan 决定。
4. 一键成片只编排，不改写业务规则。
5. 已拆开的 StoryBoard 操作仍可独立使用。

## 12. 非目标

本需求不包含：

1. 不重写 05-01 的 Segment 切分逻辑。
2. 不重写 05-02 的 provider 调用逻辑。
3. 不重写 Composer。
4. 不新增独立 04 工具来设置空镜。
5. 不绕过 StoryBoard 保存和 plan hash。
6. 不在一键成片中隐式修改 StoryBoard 文本或素材绑定。
7. 不把参考视频直接当 Final Video 使用。

## 13. 实施优先级

建议按以下顺序实施：

1. 稳定拆开的 StoryBoard 操作：VideoPlan、VideoPlan 执行、Composer。
2. 补齐 05-02 Segment / TailFrame 状态上报。
3. 补齐 Composer 状态与最终视频产物上报。
4. 新增通用 one-click-film 编排接口。
5. 在 DanceMimic 页面接入“一键成片”按钮和弹窗。
6. 将同一能力扩展到其它标准 StoryBoard 页面。

## 14. 关键设计原则

1. 一键成片是编排器，不是新执行器。
2. UI 可以把多个阶段合并为一个按钮，但状态模型必须保留 05-01、05-02、Composer 和 Segment 子步骤。
3. 默认跑整条任务，不跟随当前局部选择。
4. 默认强制刷新 VideoPlan，避免旧缓存污染成片。
5. DanceMimic 默认不跑对嘴型，走音频合成。
6. DanceMimic Segment 2+ 默认消费上一段 TailFrame 作为当前首帧。
7. 原图 / source image 不得覆盖已规划的 TailFrame 首帧链路。
8. DanceMimic Segment 2+ 不需要原图生成新图；原图只作为身份参考和 UI 展示。
9. 后续其它 StoryBoard 复用时，只复用编排能力，不复制 DanceMimic 专用业务规则。
