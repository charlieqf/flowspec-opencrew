# 一键成片通用面板与口播首版需求

版本：v0.2

状态：需求确认稿。本文用于指导后续多个“一键成片”入口的统一产品逻辑、状态模型、前后端边界和首个“视频分析（口播）一键成片”落地。

v0.2 更新：补充口播逐句状态的音频完成判定。状态面板不能只看 `SegmentAudio_Final`，必须同时识别 per-dialogue `Audio_Final`、`dialogue_audio_tasks` 和 `lipsync` 口播标记，避免 03 已生成音频但 05_02 尚未合成 SegmentAudio 时仍显示白色。

## 1. 背景

当前项目已经存在“动作模拟”的一键成片能力：

```text
OpenCrew/frontend/src/modules/koubo/DanceMimicV1/DanceMimicV1Module.jsx
OpenCrew/frontend/src/modules/koubo/DanceMimicV1/OneClickMovieDialog.jsx
OpenCrew/backend/opcrew_backend/koubo/dance_mimic_router.py
```

它的关键体验是：

1. 顶部 `一键成片` 按钮只打开运行面板。
2. 面板打开后展示当前 run、step、segment 和合成结果状态。
3. 用户在面板里点击播放 / 重新一键成片按钮后，才真正启动后台运行。
4. 后台运行状态写入独立 state JSON，前端轮询刷新。
5. 运行中、失败、服务重启失联、局部续跑都有明确状态。

后续还会出现多个一键成片需求，不能为每个场景各做一套不可复用的面板和状态协议。因此本文先沉淀通用约定，再定义“视频分析（口播）”首版需求。

## 2. 一键成片通用原则

### 2.1 入口按钮只打开面板

所有一键成片入口必须遵守：

1. 页面上的 `一键成片` 入口按钮只负责打开面板。
2. 打开面板不得自动创建 run，不得自动执行工具，不得修改 workspace。
3. 真正启动运行只能发生在面板内的主操作按钮。
4. 如果已有运行中 run，入口按钮仍可打开面板查看状态，但面板内启动按钮必须禁用。
5. 如果已有最近一次 run，入口按钮打开后展示最近一次 run 的状态、步骤、segment 和合成结果。

这一条是硬约束。后续所有“一键成片”场景都不允许做成“点击入口即运行”。

### 2.2 每个一键成片都是独立 Run Target

每个业务场景必须定义独立 target，不能共用动作模拟 target。

示例：

```text
dance_mimic_v1_one_click_movie
analysis_v1_koubo_one_click_movie
```

target 用于：

1. 状态文件 schema 的 `target` 字段。
2. 事件名命名空间。
3. 前端状态隔离。
4. 后端并发锁隔离。
5. 后续计费、审计、运行历史区分。

### 2.3 通用状态结构

每个一键成片 run 至少提供以下字段：

```json
{
  "schema_version": "one_click_movie_state_0.1",
  "task_id": 0,
  "session_id": 0,
  "run_id": "",
  "target": "",
  "status": "idle",
  "current_step_id": null,
  "steps": [],
  "segments": [],
  "compose": {},
  "summary": "",
  "created_at": 0,
  "started_at": 0,
  "finished_at": 0,
  "duration_seconds": 0,
  "updated_at": 0
}
```

通用状态值：

| 状态 | 含义 |
| --- | --- |
| `idle` | 尚未创建 run |
| `queued` | 已创建，等待后台线程/任务开始 |
| `running` | 正在执行 |
| `completed` | 全部选中步骤完成 |
| `failed` | 工具执行失败或返回非预期错误 |
| `blocked` | 输入、依赖、计划或业务规则阻断 |
| `cancelled` | 用户或系统取消 |

Step 状态：

| 状态 | 含义 |
| --- | --- |
| `pending` | 等待执行 |
| `queued` | 排队 |
| `running` | 执行中 |
| `completed` | 完成 |
| `reused` | 复用已有产物 |
| `skipped` | 本次未选中或无需执行 |
| `failed` | 失败 |
| `blocked` | 阻断 |

### 2.4 状态文件位置

每个 target 必须有“最新状态”和“历史状态”两类文件：

```text
SessionReport/<target_namespace>/one_click_movie_state.json
SessionReport/<target_namespace>/one_click_movie/<run_id>.json
```

口播首版建议：

```text
SessionReport/analysis_v1/one_click_movie_state.json
SessionReport/analysis_v1/one_click_movie/<run_id>.json
```

动作模拟现有路径保持不变，不能迁移破坏兼容性。

### 2.5 面板通用能力

一键成片面板应复用同一套体验：

1. 顶部显示 `任务 #`、`会话 #`、`尝试 #`。
2. 顶部右侧有主启动按钮、业务跳转按钮、关闭按钮。
3. 支持拖拽移动面板。
4. 默认展示折叠的 `全流程进度`。
5. 点击后展开 step 列表。
6. 默认展示折叠的 segment 状态区。
7. 点击后展开逐段 pipeline。
8. 运行中前端轮询状态。
9. 失败或阻断时保留错误摘要，不吞掉工具日志。
10. 面板关闭不影响后台运行。

### 2.6 局部续跑能力

一键成片通用 payload 必须预留：

```json
{
  "force": true,
  "resume": false,
  "run_only_step_id": "",
  "run_from_step_id": ""
}
```

语义：

1. `run_only_step_id`：只运行指定步骤。
2. `run_from_step_id`：从指定步骤开始运行到流程末尾。
3. `resume=true`：尽量复用已完成产物，只补齐缺失或失败部分。
4. `force=true`：允许重建当前流程拥有的产物。

每个 target 可以声明哪些 step 支持右键续跑。首版动作模拟已支持 `05_02`，口播首版也应至少支持 `05_02`。

### 2.7 服务重启与失联恢复

如果 state 中 run 处于 `queued` 或 `running`，但后端内存活跃任务集合中找不到该 run，应在状态查询时自动调和：

1. 当前 step 标记为 `failed`。
2. step 的 `tool_status` 标记为 `abandoned`。
3. run 总状态标记为 `failed`。
4. `summary` 写入明确提示：

```text
后台服务已重启，原一键成片运行进程已中断；请从失败步骤继续运行。
```

## 3. 口播一键成片目标

“视频分析（口播）”页面需要新增一键成片入口，把 Analysis 到 StoryBoard，再到 VideoPlan 与 Composer 的流程串成一个可观察、可续跑的完整运行。

目标不是替代现有各个手动面板，而是提供一个聚合运行面板：

1. 仍保留 `视频分析`、`脚本重写`、`音色选择`、`音色克隆`、`故事板` 等原入口。
2. 新增 `一键成片` 作为聚合入口。
3. 聚合入口只打开状态面板。
4. 面板内点击主按钮后才运行。
5. 面板状态必须和 StoryBoard VideoPlan / Composer 的真实状态一致。

## 4. 入口位置

入口位于“视频分析（口播）”顶部工具栏，放在 `视频分析` 按钮左侧。

当前参考文件：

```text
OpenCrew/frontend/src/modules/koubo/AnalysisV1/AnalysisV1Module.jsx
```

当前工具栏包含：

```text
视频分析
脚本重写
音色选择
音色克隆
故事板
折叠按钮
```

新增后：

```text
一键成片
视频分析
脚本重写
音色选择
音色克隆
故事板
折叠按钮
```

按钮要求：

1. 文案：`一键成片`。
2. 图标：使用已有播放图标或与动作模拟一致的 `PlayClipIcon`。
3. `onClick` 只设置面板打开状态，例如 `setOneClickMovieOpen(true)`。
4. 不在入口按钮点击时调用任何 POST run API。
5. 无任务时禁用。
6. 有运行中 run 时可打开面板查看状态。

## 5. 口播首版流程

口播首版一键成片包含以下步骤：

| 顺序 | Step ID | 中文名 | 工具 / 动作 |
| --- | --- | --- | --- |
| 1 | `00` | 准备会话变量 | `00_PrepareSessionVariables.py` |
| 2 | `01` | 读取视频元数据 | `01_VideoProbeMetadata.py` |
| 3 | `02_01` | 音频识别 | `02_01_AudioASR.py` |
| 4 | `02_02` | 字幕帧对齐 | `02_02_VideoSRTFrame.py` |
| 5 | `03_02` | 快速声音匹配 | `03_02_TTSBuilderQuick.py` |
| 6 | `04_01` | SRT 改写 | `04_01_SRTRewrite.py` 或自由改写模式 |
| 7 | `04_03` | 快速分组 | `04_03_StoryBoardQuick.py` |
| 8 | `05_01` | 生成视频计划 | `05_01_VideoPlanGenerator.py` |
| 9 | `05_02` | 逐句生成视频 | `05_02_VideoPlanExecutor.py` |
| 10 | `06_01` | 合并成片 | `06_01_VideoPlanComposer.py` |

首版默认使用 `04_03` 快速 StoryBoard。后续可在 payload 中扩展 `storyboard_mode=model` 支持 `04_02` 全量分组。

## 6. 口播面板展示

### 6.1 顶部

顶部显示：

```text
任务 #<task_id>
会话 #<session_id>
尝试 #<run_id>
```

右侧按钮：

1. 主按钮：`重新一键成片` 或 `一键成片`。
2. 故事板按钮：打开 `故事版（口播）`。
3. 关闭按钮。

主按钮行为：

1. 点击后调用口播一键成片 POST API。
2. 如果当前有 active Analysis run、active VideoPlan execution、active Composer execution 或 active one-click run，则禁用。
3. 如果没有任务或缺少必要模型设置，则禁用并给出 title。

### 6.2 全流程进度

默认折叠，只显示整体进度。

展开后显示 10 个 step：

```text
1  准备会话变量        完成/运行中/等待/失败    0秒
2  读取视频元数据      完成/运行中/等待/失败    0秒
...
10 合并成片            完成/运行中/等待/失败    0秒
```

进度计算参考动作模拟：

1. 已完成、复用、跳过步骤按 100% 计入。
2. 运行中步骤按当前耗时与估算耗时计算，最多计入 80%。
3. 未开始步骤计入 0。
4. 后端可返回估算耗时，前端没有估算时使用默认值。

建议估算：

| Step ID | 估算秒数 |
| --- | --- |
| `00` | 8 |
| `01` | 20 |
| `02_01` | 120 |
| `02_02` | 60 |
| `03_02` | 180 |
| `04_01` | 180 |
| `04_03` | 30 |
| `05_01` | 20 |
| `05_02` | 240 |
| `06_01` | 60 |

### 6.3 逐句成片状态

默认折叠，点击展开。

每个 segment 展示：

```text
S1  8.48s   音频 -> 新图/尾帧/首帧 -> 新视频 -> 口型同步/音频合成
S2  8.52s   音频 -> 新图/尾帧/首帧 -> 新视频 -> 口型同步/音频合成
```

状态来源必须是口播 VideoPlan 的真实状态，不允许只看一键成片 step 状态。

读取来源：

```text
SessionOutput/storyboard/video_generation_plan.json
SessionOutput/storyboard/video_plan_execution_state.json
SessionOutput/storyboard/video_plan_execution_result.json
SessionOutput/storyboard/video_plan_compose_state.json
SessionOutput/storyboard/video_plan_compose_result.json
SessionOutput/storyboard/koubo_storyboard_edit.json
SessionOutput/storyboard/srt_storyboard.json
```

每段至少返回：

```json
{
  "index": 1,
  "segment_id": "",
  "asset_key": "",
  "dialogue_ids": [],
  "status": "pending",
  "duration_seconds": 0,
  "steps": {},
  "files": {
    "audio": {},
    "first_frame": {},
    "raw_video": {},
    "final_video": {},
    "tail_frame": {}
  },
  "lipsync": {
    "need_lipsync": true,
    "sync_mode": "",
    "reason": ""
  },
  "error": ""
}
```

显示标签规则：

1. 音频：读取 dialogue audio 或 segment audio 状态。
2. 图像：按 plan 的 first frame 来源显示 `新图`、`尾帧`、`首帧`、`绑定图`。
3. 视频：raw video / generated video 状态。
4. 同步：如果 `need_lipsync=true` 或 `sync_mode=lipsync`，显示 `音频匹配`；如果 `sync_mode=audio_replace_retime`，显示 `音频合成`。

音频状态完成态的优先级：

1. StoryBoard / plan 已绑定 per-dialogue 音频，例如 `Audio_Final` 或 `dialogue_audio_tasks[].existing_audio_path`，则显示完成。
2. `05_02` 已生成 segment 合成音频，例如 `SegmentAudio_Final`，则显示完成。
3. 人物口播 plan 明确 `sync_mode=lipsync` 且 `lipsync_reason=dialogue_marked_talking_head`，说明该段走口播音频匹配链路；只要前序 03 已完成或 plan 中存在 dialogue 音频任务，面板应按“音频已匹配”显示完成，而不是等待 `SegmentAudio_Final`。
4. 只有三类信号都缺失时，才显示等待音频匹配。

这条规则专门防止一个常见误判：03 已经真实生成每句克隆声音音频，但 `analysis_v1_one_click_segments` 等运行摘要只汇总了 `planned_outputs.segment_audio_path`。此时刷新页面不会变绿，因为状态摘要缺少 per-dialogue 音频信息；前端 adapter 或后端状态汇总必须把 dialogue audio / lipsync 口播标记纳入完成态判断。

## 7. 后端 API

建议新增：

```text
POST /api/openclip/tasks/{task_id}/analysis-v1/one-click-movie
GET  /api/openclip/tasks/{task_id}/analysis-v1/one-click-movie
GET  /api/openclip/tasks/{task_id}/analysis-v1/one-click-movie/{run_id}
```

POST payload：

```json
{
  "force": true,
  "resume": false,
  "run_only_step_id": "",
  "run_from_step_id": "",
  "tts_builder_mode": "quick",
  "rewrite_mode": "strict",
  "storyboard_mode": "quick",
  "video_plan_settings": {
    "max_video_seconds": 4,
    "min_video_seconds": 2,
    "split_tolerance_seconds": 2
  },
  "composer_settings": {
    "subtitle_mode": "hyperframe",
    "watermark_mode": "always"
  }
}
```

GET response：

```json
{
  "ok": true,
  "task_id": 0,
  "session_id": 0,
  "run_id": "",
  "target": "analysis_v1_koubo_one_click_movie",
  "status": "idle",
  "current_step_id": null,
  "steps": [],
  "segments": [],
  "compose": {},
  "summary": "",
  "workspace_dir": "",
  "updated_at": 0
}
```

## 8. 执行编排

### 8.1 00 到 04_03

前置步骤应复用现有 Analysis V1 run-to-storyboard 逻辑：

```text
/api/openclip/tasks/{task_id}/analysis-v1/run-to-storyboard
```

但口播一键成片需要独立 target 和独立状态，不应直接把 run-to-storyboard 的 attempt UI 当成一键成片 UI。

实现建议：

1. 后端共用已有 `analysis_v1_run_step_specs`、step payload、工具执行函数。
2. 一键成片 state 中记录这些 step 的状态。
3. 仍可在底层生成 Analysis attempt，便于审计。
4. 前端一键成片面板只展示聚合状态，不跳到原 run progress dialog。

### 8.2 05_01

`05_01` 必须生成整片 task scope VideoPlan。

调用语义：

```text
05_01_VideoPlanGenerator.py
--target-type task
--max-video-seconds <settings.max_video_seconds>
--min-video-seconds <settings.min_video_seconds>
--split-tolerance-seconds <settings.split_tolerance_seconds>
--force
--print-json
```

注意：

1. 口播一键成片的成片目标是整片，不使用当前 StoryBoard 页面选中的 scene 或 shot scope。
2. 如果已有 task scope plan 与当前 StoryBoard、媒体绑定、参数一致，可按策略标记 `reused`。
3. 如果 plan scope 不是 task，必须重新生成 task scope plan。

### 8.3 05_02

`05_02` 必须执行当前 `video_generation_plan.json`。

口播版不得照搬动作模拟的一键成片参数。

动作模拟使用了：

```text
--no-execute-lipsync
--execute-audio-video-sync
first_frame_policy = previous_segment_tail_frame
```

口播版不应强制这些策略。口播版必须沿用当前 `05_01` plan 中的 `tasks.need_lipsync`、`tasks.sync_mode`、first frame source、素材绑定和口播/空镜人工标记。

### 8.3.1 音频匹配与音频合成边界

口播一键成片必须保留口播主链路的音频匹配与音频合成逻辑，不允许降级成动作模拟的强制音频替换链路。

音频匹配：

1. `03_02` 快速声音匹配仍然是口播一键成片流程中的正式步骤。
2. `03_02` 负责为 dialogue / segment 准备可用的口播音频素材。
3. StoryBoard 中的 per-dialogue 音频槽仍然使用 `Audio_Final`。
4. VideoPlan 执行阶段消费的 segment 合成音频仍然使用 `SegmentAudio_Final`。
5. `Audio_Final` 和 `SegmentAudio_Final` 不能混为一个状态：前者代表单句音频槽，后者代表可供视频生成、口型同步或音频替换使用的 segment 级合成音频。
6. 一键成片状态面板的“音频”绿灯不能只等 `SegmentAudio_Final`；口播场景下，per-dialogue `Audio_Final` 已存在且 plan 标记 `lipsync` 时，应显示为 `音频已匹配`。

音频合成：

1. `05_02` 仍然需要按 segment 生成 / 复用 `SegmentAudio_Final`。
2. 如果 `tasks.need_lipsync=true`，`05_02` 使用 segment 音频和 raw video 进入口型同步，最终视频是对嘴后视频。
3. 如果 `tasks.need_lipsync=false` 且 `tasks.sync_mode=audio_replace_retime`，`05_02` 才走音频替换 / 重定时，让最终视频时长贴合 segment 音频。
4. 口播版不能全局强制所有 segment 都走 `audio_replace_retime`。
5. 口播版不能全局强制关闭 lipsync。
6. 面板里的逐句状态应根据 `need_lipsync` / `sync_mode` 显示 `音频匹配` 或 `音频合成`，而不是固定显示某一种。
7. 人物口播第六步固定调用 `Analysis_V1/05_01_VideoPlanGenerator.py`；口播页面复用通用 `OneClickMovieDialog` 时必须显式传入基于计划字段的 `syncLabel`，不得落入通用面板面向动作模拟的“音频合成”默认文案。
8. 标签和状态提示必须使用同一判定函数：口播段的标签为“音频匹配”时，tooltip/等待文案也必须是“等待音频匹配”，不得仍写“等待音频合成”。
9. 人物口播复用通用一键成片弹窗时，必须同时接入右键续跑动作：“继续完成该步”只运行 `05_02`；“继续完成后续步骤”从 `05_02` 开始运行。两个动作都使用 `force=false`、`resume=true`，不得因为可选回调未传而静默无响应，也不得误回退到默认的 `05_01` 起点。
10. Analysis_V1 与 TalkingHead_V1 的 SDR2V 提示词预算保持一致：最终提示词最多 1000 个字符，固定模板最多 700 个字符，至少为完整对白预留 300 个字符；超限必须显式报错，不得截断或改写对白。

动作模拟的一键成片为了避免口型同步，显式追加 `--no-execute-lipsync` 和 `--execute-audio-video-sync`。这只是动作模拟 profile 的业务约束；口播 profile 必须保持 `05_01` / `05_02` 原有的口播音频决策。

调用语义应与 StoryBoard VideoPlan 执行保持一致：

```text
05_02_VideoPlanExecutor.py
--workspace <workspace>
--force
--execution-job-id <run_id_or_child_job_id>
--source-plan-hash <current_plan_hash>
--print-json
```

是否追加 provider/model 参数，由当前全局媒体模型配置和现有 VideoPlan execute route 决定。

### 8.4 06_01

`06_01` 必须执行整片 task scope 合成。

调用语义：

```text
06_01_VideoPlanComposer.py
--workspace <workspace>
--target-type task
--subtitle-mode <composer_settings.subtitle_mode>
--watermark-mode <composer_settings.watermark_mode>
--force
--print-json
```

合成结果写入：

```text
SessionOutput/storyboard/video_plan_compose_result.json
```

一键成片 `compose` 字段应至少返回：

```json
{
  "exists": true,
  "status": "completed",
  "output_video": "",
  "summary": {}
}
```

## 9. 并发与锁

口播一键成片启动前必须检查：

1. 当前是否已有 Analysis V1 run-to-storyboard active attempt。
2. 当前是否已有 VideoPlan execution running。
3. 当前是否已有 Composer execution running。
4. 当前是否已有口播 one-click movie running。

如果存在冲突：

1. POST 返回 `409`。
2. response detail 包含冲突类型和 active id。
3. 前端面板显示冲突提示，不关闭面板。

允许：

1. 打开面板查看状态。
2. GET 当前状态。
3. 查看最近一次失败或完成结果。

不允许：

1. 同一个 task 同时启动两个口播一键成片。
2. 一键成片运行中手动执行 05_02 或 06_01。
3. 05_02 运行中修改口播/空镜标记或保存 StoryBoard。

## 10. 续跑规则

首版至少支持以下续跑：

| 操作 | Payload | 含义 |
| --- | --- | --- |
| 继续完成 05_02 | `run_only_step_id=05_02` | 只按当前 VideoPlan 补跑逐句视频 |
| 继续完成后续步骤 | `run_from_step_id=05_02` | 运行 `05_02 -> 06_01` |
| 从 05_01 重新开始 | `run_from_step_id=05_01` | 重新生成 plan、执行、合成 |
| 全量重跑 | 空 step override + `force=true` | 重新运行完整流程 |

右键菜单首版可只暴露在 `05_02` step 上，与动作模拟保持一致。后续可以扩展到 `05_01` 和 `06_01`。

## 11. 前端文件建议

建议新增或拆分：

```text
OpenCrew/frontend/src/modules/koubo/AnalysisV1/OneClickMovieDialog.jsx
OpenCrew/frontend/src/modules/koubo/AnalysisV1/analysisV1OneClickMovie.js
```

复用或参考：

```text
OpenCrew/frontend/src/modules/koubo/DanceMimicV1/OneClickMovieDialog.jsx
OpenCrew/frontend/src/modules/koubo/DanceMimicV1/DanceMimicV1Module.jsx
OpenCrew/frontend/src/modules/koubo/KouboStoryBoard/kouboStoryboardVideoPlan.js
OpenCrew/frontend/src/modules/koubo/KouboStoryBoard/kouboStoryboardComposer.js
```

不要把一键成片面板的大段 UI 直接塞进 `AnalysisV1Module.jsx`。

首版允许先复制动作模拟面板后改名，但必须抽出可复用的 display helpers，避免后续第三个一键成片继续复制。

建议抽象边界：

1. 通用：弹窗布局、拖拽、step 状态、进度条、右键菜单外壳。
2. 场景自定义：step labels、segment pipeline、启动 API、故事板跳转。

## 12. 后端文件建议

建议新增：

```text
OpenCrew/backend/opcrew_backend/koubo/analysis_v1_one_click_movie.py
```

或在现有 `router.py` 中先落地，再在第二步拆出服务文件。

但不建议把口播一键成片逻辑写入 `dance_mimic_router.py`。动作模拟 router 只能作为参考，不应成为口播 target 的承载文件。

建议后端模块拆分：

1. state path / read / write。
2. compile plan。
3. selected specs / unselected step marking。
4. command builder。
5. segment status adapter。
6. compose summary adapter。
7. stale running reconciliation。
8. POST / GET routes。

## 13. 与动作模拟的明确边界

口播版参考动作模拟，但不能复制以下业务假设：

| 动作模拟假设 | 口播要求 |
| --- | --- |
| `--no-execute-lipsync` | 不强制禁用 lipsync |
| `--execute-audio-video-sync` | 按 plan 的 sync mode 执行 |
| 后续段使用上一段尾帧作为首帧 | 按口播 VideoPlan 的 first frame source 执行 |
| 动作参考视频驱动 | 口播按 StoryBoard、人物/产品参考、口播/空镜标记执行 |
| target 为 `dance_mimic_v1_one_click_movie` | target 为 `analysis_v1_koubo_one_click_movie` |

动作模拟现有功能不得被口播改造影响。

## 14. 验收标准

### 14.1 入口与面板

1. “视频分析（口播）”顶部 `视频分析` 左侧出现 `一键成片` 按钮。
2. 点击入口按钮只打开面板，不发起 POST，不创建 run，不改 workspace。
3. 面板内点击主按钮后才启动运行。
4. 有运行中 run 时，入口按钮可打开面板查看进度，主按钮禁用。
5. 面板可关闭，后台运行不受影响。

### 14.2 流程与状态

1. 面板展示 00、01、02_01、02_02、03_02、04_01、04_03、05_01、05_02、06_01。
2. 每一步显示状态和耗时。
3. 运行中状态可轮询刷新。
4. 状态轮询不得反复刷新一键成片结果播放器；同一 `run_id`、同一输出路径必须保持稳定的视频 URL 和播放器实例，不中断播放进度、暂停状态、音量或全屏。只有输出路径变化、新 run 创建或用户主动刷新时才允许更新视频资源。
5. 失败状态保留失败 step、message、stdout/stderr tail。
6. 服务重启导致失联时，状态调和为 failed，并提示可从失败步骤继续。

### 14.3 VideoPlan 一致性

1. `05_01` 生成 task scope plan。
2. `05_02` 执行当前 plan hash。
3. `06_01` 合成 task scope 整片。
4. 逐句状态读取真实 VideoPlan execution state/result 和 Working 文件。
5. 文件存在优先显示完成，运行中显示运行中，失败显示失败，缺依赖显示等待或阻断。
6. StoryBoard 或媒体绑定变更导致 plan 过期时，一键成片阻断或重新生成 plan，不允许继续执行旧 plan。

### 14.4 口播业务边界

1. 口播版不强制 `--no-execute-lipsync`。
2. 口播版不强制所有后续段使用上一句尾帧。
3. 口播/空镜人工标记必须继续生效。
4. 已有动作模拟一键成片按钮、面板、API、状态文件不受影响。

## 15. 首版不做

1. 不把动作模拟和口播的一键成片后端合并成一个大 router。
2. 不做跨 task 批量一键成片。
3. 不做多用户并发调度。
4. 不在入口按钮点击时自动运行。
5. 不把口播一键成片写入 StoryBoard 底部 Timeline；首版入口在 Analysis V1 顶部。
6. 不改变现有 VideoPlan Modal、Composer Modal 的手动使用方式。

## 16. 后续扩展方向

后续新增其它一键成片需求时，只需要补充一个 profile：

```json
{
  "target": "",
  "entry_surface": "",
  "state_namespace": "",
  "steps": [],
  "segment_adapter": "",
  "compose_adapter": "",
  "start_api": "",
  "status_api": "",
  "business_guards": []
}
```

通用面板不关心具体业务，只消费：

1. `steps`
2. `segments`
3. `compose`
4. `status`
5. `summary`
6. `capabilities`

业务差异由 profile 和 adapter 提供。这样后续的“一键成片”需求可以复用同一套运行面板、状态协议和续跑体验。
