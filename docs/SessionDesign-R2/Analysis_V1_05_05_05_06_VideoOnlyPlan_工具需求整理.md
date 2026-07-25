# Analysis_V1 05_05_VideoOnlyPlanGenerator / 05_06_VideoOnlyPlanExecutor 工具需求整理

版本：v0.1

状态：需求确认稿。本文用于确认新增 Video Only 计划与执行工具的边界、目录合同、输入输出、与 `05_01 / 05_02` 的衔接方式，以及 StoryBoard 作为唯一业务金标准的规则。

## 1. 背景

当前 `05_01_VideoPlanGenerator` 和 `05_02_VideoPlanExecutor` 是完整视频执行链：

```text
AUDIO -> First Frame -> Video Prompt -> VIDEO -> SYNC
```

其中 `SYNC` 包含对嘴型或音画同步逻辑。现在需要新增一组 Video Only 工具，让用户可以先生成视频本体，再决定该视频是否直接作为最终视频，或者后续只补最后的对嘴型。

新增工具：

```text
05_05_VideoOnlyPlanGenerator.py
05_06_VideoOnlyPlanExecutor.py
```

Video Only 的执行链固定为：

```text
AUDIO -> First Frame -> Video Prompt -> VIDEO
```

本流程不执行 Sync，不调用 lipsync provider，不生成 Lipsync request / response。

## 2. 工具名称与推荐顺序

硬性确认：

1. 不修改现有 `05_01_VideoPlanGenerator.py` 名称。
2. 不修改现有 `05_02_VideoPlanExecutor.py` 名称。
3. 新增 Video Only 计划工具命名为 `05_05_VideoOnlyPlanGenerator.py`。
4. 新增 Video Only 执行工具命名为 `05_06_VideoOnlyPlanExecutor.py`。

推荐 Analysis_V1 工具编号和可选执行组：

```text
S1_00_PrepareSessionVariables
S2_01_VideoProbeMetadata
S3_02_01_AudioASR
S4_02_02_VideoSRTFrame
S5_03_01_TTSBuilderG
S6_04_01_SRTRewrite
S7_04_02_StoryBoard

视频完整执行组:
  S8_05_01_VideoPlanGenerator
  S9_05_02_VideoPlanExecutor

图像可控子流程执行组:
  S10_05_03_ImagePlanGenerator
  S11_05_04_ImagePlanExecutor

Video Only 子流程执行组:
  S12_05_05_VideoOnlyPlanGenerator
  S13_05_06_VideoOnlyPlanExecutor

后续拼接:
  S14_06_01_VideoPlanComposer
```

说明：

1. `S12 / S13 / S14` 是当前推荐顺序，不是工具名的一部分。
2. 真实运行仍遵守 `S{step_index}_{tool_name}` 目录规则。
3. `05_05 + 05_06` 是 Video Only 子流程，不替代 `05_01 + 05_02`。
4. 用户可以先运行 `05_05 + 05_06` 生成 Raw Video，再决定是否把它确认为 Final Video。
5. 如果 Raw Video 未确认为 Final，后续运行 `05_01 + 05_02` 时应复用 StoryBoard 中已有 Raw，只补最后 Sync。

## 3. 核心原则

### 3.1 StoryBoard 是唯一业务金标准

跨工具判断不得依赖某个工具自己的 `Working/` 目录。

唯一业务金标准是：

```text
SessionOutput/storyboard/Working/
SessionOutput/storyboard/srt_storyboard.json
SessionOutput/storyboard/koubo_storyboard_edit.json
```

规则：

1. `S9_05_02_VideoPlanExecutor/Working/` 只表示 `05_02` 的执行快照。
2. `S13_05_06_VideoOnlyPlanExecutor/Working/` 只表示 `05_06` 的执行快照。
3. 两个工具之间不得通过彼此的 `Working/` 目录交接。
4. Raw Video 是否可供 `05_02` 继续 Sync，必须通过 `SessionOutput/storyboard/Working/{asset_key}_Video_Raw.mp4` 判断。
5. Final Video 是否已完成，必须通过 `SessionOutput/storyboard/Working/{asset_key}_Video_Final.mp4` 及 StoryBoard JSON 绑定判断。
6. 工具执行完成后，必须把对应业务状态同步到 StoryBoard，而不是只写自己的工具目录。

### 3.2 不新增业务状态字段

第一版不新增类似以下业务字段：

```text
video_only_status
confirmed_as_final_video
raw_video_candidate_path
```

状态由现有文件命名和现有 StoryBoard 绑定表达。

Raw Video：

```text
SessionOutput/storyboard/Working/{asset_key}_Video_Raw.mp4
```

Final Video：

```text
SessionOutput/storyboard/Working/{asset_key}_Video_Final.mp4
```

Raw 是否存在、Final 是否存在、Final 是否绑定，是后端和前端判断状态的依据。

### 3.3 Raw 不等于 Final

Raw Video 和 Final Video 的语义必须分开。

1. Raw Video 表示 `VIDEO` 步骤已完成，但尚未确认可直接交付。
2. Final Video 表示当前 Segment 的最终业务视频已完成。
3. 用户点击“确认是最终视频”后，工具或后端只需要把 Raw 拷贝成 Final 规范文件名，并同步 StoryBoard 视频绑定。
4. 用户未确认时，Raw 留在 StoryBoard Working，供 `05_02` 后续只执行 Sync；talking head 走 Lip Sync，空镜 / cutaway 走 audio_replace_retime / 音画同步。
5. Raw 文件不能被 `05_01` 误判为绑定视频完成态；`05_01` 的 bound video 仍只认现有 StoryBoard 视频绑定和 Final 语义。

## 4. 05_05_VideoOnlyPlanGenerator 边界

`05_05_VideoOnlyPlanGenerator` 是计划工具，不调用任何模型。

它负责：

1. 读取当前已保存的 StoryBoard。
2. 复用 `05_01` 的 Scene / Shot / Task 范围、首帧来源、切段、尾帧依赖和 blocked / skipped 规则。
3. 生成 Video Only 计划。
4. 每个 segment 只规划以下步骤：

```text
audio
first_frame
video_prompt
video
```

5. 输出 `video_only_generation_plan.json`。
6. 不规划 `sync` 步骤。
7. 不规划 `lipsync` provider。
8. 不修改 StoryBoard 的 Shot / Scene / Dialogue 结构。

它不得：

1. 调用 TTS / 图片 / 视频 / lipsync 模型。
2. 生成图片或视频 Prompt。
3. 写入 Raw Video 或 Final Video。
4. 把 Raw Video 绑定为 Final Video。
5. 创建新的图片或视频 Prompt 模板体系。

## 5. 05_06_VideoOnlyPlanExecutor 边界

`05_06_VideoOnlyPlanExecutor` 是 Video Only 执行器。

它负责：

1. 读取 `video_only_generation_plan.json`。
2. 按计划执行 Audio。
3. 按计划执行 First Frame。
4. 按计划生成 Video Prompt。
5. 调用视频模型生成 Raw Video。
6. 将 Raw Video 发布到 StoryBoard Working：

```text
SessionOutput/storyboard/Working/{asset_key}_Video_Raw.mp4
```

7. 从 Raw Video 抽取尾帧并发布到 StoryBoard Working：

```text
SessionOutput/storyboard/Working/{asset_key}_TailFrame.png
```

该 Raw 尾帧只允许作为诊断 / 预览产物记录，不能解除下游 `previous_segment_tail_frame` 或 `previous_scene_tail_frame` 依赖。

8. 生成执行状态和审计结果。
9. 在用户确认 Raw 为 Final 时，将 Raw 拷贝为：

```text
SessionOutput/storyboard/Working/{asset_key}_Video_Final.mp4
```

并从当前 Final 重新抽取 `SessionOutput/storyboard/Working/{asset_key}_TailFrame.png`，同步 StoryBoard JSON 中对应 Dialogue 的视频绑定。只有 Final 文件、TailFrame 文件和 JSON 绑定同时成功后，下游才允许消费该尾帧。

它不得：

1. 执行 Sync。
2. 调用 lipsync provider。
3. 执行 `audio_replace_retime`。
4. 生成或写入 Lipsync request / response。
5. 把未确认的 Raw 自动改名为 Final。
6. 依赖 `05_02` 的 Working 目录。

## 6. 文件合同

### 6.1 05_05 工具目录

```text
S12_05_05_VideoOnlyPlanGenerator/
  Working/
  Output/
  Report/
```

主要输出：

```text
S12_05_05_VideoOnlyPlanGenerator/Output/video_only_generation_plan.json
S12_05_05_VideoOnlyPlanGenerator/Report/Result.json
SessionOutput/storyboard/video_only_generation_plan.json
```

### 6.2 05_06 工具目录

```text
S13_05_06_VideoOnlyPlanExecutor/
  Working/
  Output/
  Prompt/
  Report/
```

`Working/` 中可以保存模型下载文件、临时 Raw、首帧快照、Provider task state 等执行快照。

业务输出必须发布到：

```text
SessionOutput/storyboard/Working/
```

### 6.3 StoryBoard Working 业务文件

规范业务文件：

```text
SessionOutput/storyboard/Working/{asset_key}_Audio_Final.wav
SessionOutput/storyboard/Working/{asset_key}_SegmentAudio_Final.wav
SessionOutput/storyboard/Working/{asset_key}_Image_01.png
SessionOutput/storyboard/Working/{asset_key}_ImagePrompt.json
SessionOutput/storyboard/Working/{asset_key}_VideoPrompt.json
SessionOutput/storyboard/Working/{asset_key}_Video_Raw.mp4
SessionOutput/storyboard/Working/{asset_key}_Video_Final.mp4
SessionOutput/storyboard/Working/{asset_key}_TailFrame.png
```

其中：

1. `{asset_key}_Video_Raw.mp4` 是 Video Only 生成结果，也是 `05_02` 后续 Sync 的输入候选。
2. `{asset_key}_Video_Final.mp4` 是最终业务视频。
3. 未确认 Final 时，不能把 Raw 绑定为 StoryBoard 的最终视频槽。
4. 确认 Final 后，必须把 Final 路径同步绑定到 `srt_storyboard.json` 和 `koubo_storyboard_edit.json`，并从 Final 抽取 / 刷新同 key `TailFrame.png`。
5. 如果 Final 已存在但 TailFrame 缺失、来自旧视频或只完成 JSON 补绑定，也必须从当前 Final 补抽 `TailFrame.png` 后才能把该 Segment 视为下游可消费。

## 7. UI 控制规则

Video Only Modal 或 VideoPlan 面板中，每个 Segment 展示：

```text
AUDIO -> First Frame -> Prompt -> VIDEO -> Confirm Final
```

顶部执行按钮与 ImagePlan 保持同一模式，提供三个按钮：

```text
Prompt
Video
Prompt + Video
```

按钮语义：

1. `Prompt`：只执行 Video Prompt Step，不调用视频模型。
2. `Video`：不重新生成 Prompt，只读取当前业务 Video Prompt 生成 Raw Video。
3. `Prompt + Video`：先生成或刷新 Video Prompt，再立即使用当前业务 Prompt 生成 Raw Video。
4. 如果当前 Segment 已经存在 Raw，`Prompt`、`Video`、`Prompt + Video` 全部 disabled；页面只允许 `Confirm Final` 或受控清理 / 重新生成 Raw 流程。
5. 如果当前 Segment 已经存在 Final，`Prompt`、`Video`、`Prompt + Video` 全部 disabled，除非用户先按受控清理流程清除 Final / Raw。
6. `Video` 模式必须要求 `SessionOutput/storyboard/Working/{asset_key}_VideoPrompt.json` 已存在；只有 `Image_New` 存在但 VideoPrompt 缺失时，Raw 不能显示白色可执行，必须先执行 Prompt。

每个 Segment 的 VIDEO 后提供手动操作：

```text
确认是最终视频
```

交互规则：

1. `Audio` 和 `First Frame` 是 Video 生成前的必要准备步骤，但顶部不单独提供 Audio / First Frame 按钮。
2. Video Only Plan 中 `Audio` 的完成态必须与完整 VideoPlan 保持一致：只要当前 Segment 的 `dialogue_audio_tasks` 中每条 Dialogue 已有可用的 `Audio_Final.wav`（或 `planned_audio_path / existing_audio_path` 指向 StoryBoard Working 中存在的音频），`Audio` 步骤即可显示绿色完成态。
3. 如果当前 Segment 有 `dialogue_audio_tasks`，Audio 完成态必须以 Dialogue 级 `Audio_Final.wav` 为准；旧的 `{asset_key}_SegmentAudio_Final.wav` 只能作为没有 Dialogue 音频明细时的兜底，不能在 Dialogue 音频已被清空后继续把 Audio 显示为绿色。
4. 清空 Dialogue Audio 时只清空 Audio 本身，不级联删除或解绑 `{asset_key}_SegmentAudio_Final.wav`、`{asset_key}_Video_Raw.mp4`、`{asset_key}_Video_Final.mp4`、`{asset_key}_TailFrame.png` 或视频绑定；这些下游内容只能由用户手动触发的受控清理 / 重生成流程处理。
5. `{asset_key}_SegmentAudio_Final.wav` 不要求在打开 Video Only Plan Modal 前已经存在；它是执行 `05_06` 的 Segment 前置步骤时，由 Dialogue 音频拼接生成并发布到 StoryBoard Working 的业务输出。
6. 执行 `Video` 时，如果缺少必要 Audio 或 First Frame，`05_06` 可以按 plan 先补齐这些前置步骤，再执行视频模型。
7. Raw 生成成功后，VIDEO 步骤显示完成。
8. 如果 `{asset_key}_Video_Raw.mp4` 存在但 `{asset_key}_Video_Final.mp4` 不存在，VIDEO 显示为 Raw 完成、待确认或待 Sync。
9. 用户点击“确认是最终视频”后，后端把 Raw 拷贝为 Final，从 Final 抽取 / 刷新 `TailFrame.png`，并同步 StoryBoard 视频绑定。
10. 确认完成后，该 Segment 在完整 VideoPlan 中应显示 Final Video 已完成。
11. 未确认时，不修改 Final 绑定；后续 `05_02` 可以接续 Raw 做 Sync。
12. 未确认 Final 时，下一个 Segment 不能使用当前 Raw 的尾帧作为首帧来源继续生成；否则会与 `05_01 / 05_02` 的 Final Video 状态冲突。
13. 确认 Final 且 `TailFrame.png` 抽取成功后，下一个 Segment 才可以使用当前 Final Video 抽取出的尾帧作为首帧来源。
14. 确认 Final 成功后，依赖该 Segment 尾帧的下一个 Segment 应立即从等待 / blocked 状态解除；如果 TailFrame 抽取失败，则仍保持 blocked。
15. 当前 StoryBoard dirty 时，不允许执行 Prompt / Video / Prompt + Video，也不允许确认 Final；必须先保存 StoryBoard。
16. `05_02` 或 `05_06` 正在执行时，三个执行按钮和确认按钮全部 disabled。
17. Confirm Final 不依赖 Audio 完成态；只要 Raw 存在且非空，或存在可补绑定的 Final，确认按钮即可进入可执行 / 待确认状态。缺音频不能阻止 Raw 拷贝成 Final。

槽位颜色优先级：

1. `VideoPrompt.json` 存在时 Prompt 绿色；不存在但 `Image_New` 已存在时 Prompt 白色可执行。
2. `Video_Raw.mp4` 存在时 Raw / Video 绿色；此时即使 Prompt 缺失，也不能把 Raw 回退成灰色或白色。
3. `Video_Raw.mp4` 不存在时，只有 `Image_New` 和 `VideoPrompt.json` 都存在，Raw 才能白色可执行；只有 `Image_New` 时 Raw 必须灰色等待 Prompt。
4. `Video_Final.mp4` 存在且绑定、同 key `TailFrame.png` 有效时 Confirm Final 绿色。
5. Raw 存在但 Final 未确认时，Confirm Final 白色可执行；该判断不读取 Audio 状态。
6. 当前 running / failed 只在规范业务文件不存在时影响颜色；已落盘 Raw / Final 优先绿色。

页面布局规则：

1. 顶部三个执行按钮只负责触发命令和执行中 disabled。
2. 黄色运行态和绿色完成态必须显示在下方具体 segment 的 `Prompt` / `Video` / `Confirm Final` 步骤卡片上。
3. 多 Shot 展示必须纵向排列；不能把多个 Shot 横向挤在同一行。
4. 每个 Shot 内部可以紧凑展示 Segment，但 Prompt / Video / Confirm Final 的状态不能互相覆盖。

## 8. 05_02 最小衔接改动

`05_02_VideoPlanExecutor` 需要增加最小衔接逻辑：在判断当前 Segment 的 Video 步骤是否已经完成时，必须优先读取 StoryBoard 的统一业务状态，而不是只看自己的工具 Working。

### 8.1 判断顺序

`05_02` 执行某个 segment 时，Video 前置判断顺序如下：

```text
1. StoryBoard Working 中已有 {asset_key}_Video_Final.mp4，且 StoryBoard JSON 已绑定
   -> 当前 Segment 已有最终视频。
   -> 不重新生成 Video。
   -> 不执行 Video / Sync。

2. StoryBoard Working 中没有 Final，但已有 {asset_key}_Video_Raw.mp4
   -> 当前 Segment 的 Video 步骤已完成。
   -> 将 Raw 拷贝到 S9_05_02_VideoPlanExecutor/Working/。
   -> 跳过 Video Prompt / Video 模型生成。
   -> 后续只执行 Sync 阶段。
   -> 如果是 talking head，则 Sync 通常是 Lip Sync。
   -> 如果是空镜 / cutaway，则 Sync 仍按 05_02 原逻辑执行 audio_replace_retime / 音画同步。
   -> Sync 成功后发布 {asset_key}_Video_Final.mp4 并同步 StoryBoard JSON。

3. StoryBoard Working 中既没有 Final 也没有 Raw
   -> 按 05_02 原逻辑执行 Audio / First Frame / Video Prompt / Video / Sync。
```

### 8.2 Raw 拷贝到 05_02 Working

因为 `05_02` 和 `05_06` 不共享工具 Working，`05_02` 继续执行 Sync 前必须把 StoryBoard Raw 拷贝到自己的 Working：

```text
from:
  SessionOutput/storyboard/Working/{asset_key}_Video_Raw.mp4

to:
  S9_05_02_VideoPlanExecutor/Working/{asset_key}_Video_Raw.mp4
```

Sync 只能读取 `05_02` 自己 Working 中的 Raw 快照，不能直接依赖 `05_06` 的工具目录。

### 8.3 05_02 同步 Video 状态到 StoryBoard

`05_02` 自己生成 raw video 后，也必须同步到 StoryBoard Working，使 StoryBoard 成为所有执行任务的唯一金标准。

规则：

1. `05_02` 生成 raw video 后，先写入自己的 Working。
2. Raw 必须发布到 StoryBoard Working：

```text
SessionOutput/storyboard/Working/{asset_key}_Video_Raw.mp4
```

3. Raw 发布成功后，Video Step 即为 `completed_working`；后续 Final 不属于 Video Step，而属于 Sync Step 的输出。
4. Sync Step 按 `05_02` 原逻辑继续执行：
   - talking head：Lip Sync。
   - 空镜 / cutaway：audio_replace_retime / 音画同步。
5. Sync 成功后，再发布：

```text
SessionOutput/storyboard/Working/{asset_key}_Video_Final.mp4
```

6. Final 发布后，必须同步绑定 `srt_storyboard.json` 和 `koubo_storyboard_edit.json`。
7. Raw 发布不等于 Final 绑定；Raw 只表示 Video 步骤已完成，可供后续 Sync 使用。
8. 如果 Raw 发布失败，不能把 Video 步骤显示为完成。
9. 如果 Sync 失败，不能回退 Video Step；Video 仍保持 Raw 完成，Sync 显示失败。

## 9. 与 05_01 的关系

`05_01` 仍负责完整 VideoPlan 的计划生成。

第一版最小改动原则：

1. `05_01` 不需要读取 `S13_05_06_VideoOnlyPlanExecutor/Working/`。
2. `05_01` 可以继续按照现有 StoryBoard 状态生成 plan。
3. 如果 StoryBoard 已经绑定 Final Video，`05_01` 按现有 bound video / final video 逻辑处理。
4. 如果 StoryBoard 只有 Raw Video，`05_01` 不应把 Raw 当作 Final bound video。
5. `05_02` 执行时负责识别 Raw 并进入最小 Sync 路径。
6. `05_01` 规划后续 Segment 继承尾帧时，只允许继承已确认 Final 的尾帧；未确认 Raw 的尾帧不得作为下一个 Segment 的首帧来源。

如果后续需要让 `05_01` 在 plan 中显式展示 Raw 已存在，可以通过 artifact status 或 UI payload 补充，不在第一版新增 StoryBoard 字段。

## 10. Prompt 与 Provider 复用

`05_06` 必须复用现有 `05_02` 的图片和视频 Prompt / provider 模块。

模板唯一来源：

```text
OpenCrew/ToolLibrary/Analysis_V1/Reference/05_02/Image_GPT.md
OpenCrew/ToolLibrary/Analysis_V1/Reference/05_02/Image_Gemini.md
OpenCrew/ToolLibrary/Analysis_V1/Reference/05_02/Image_Grok.md
OpenCrew/ToolLibrary/Analysis_V1/Reference/05_02/Video_GPT.md
OpenCrew/ToolLibrary/Analysis_V1/Reference/05_02/Video_Gemini.md
OpenCrew/ToolLibrary/Analysis_V1/Reference/05_02/Video_Grok.md
OpenCrew/ToolLibrary/Analysis_V1/Reference/05_02/Video_OpenRouter.md
OpenCrew/ToolLibrary/Analysis_V1/Reference/05_02/Video_Seedance.md
OpenCrew/ToolLibrary/Analysis_V1/Reference/05_02/Video_Wan.md
```

Python 模块唯一来源：

```text
OpenCrew/ToolLibrary/Analysis_V1/video_plan_executor_modules/image_*.py
OpenCrew/ToolLibrary/Analysis_V1/video_plan_executor_modules/video_*.py
```

规则：

1. 不新增第二套图片 Prompt 模板。
2. 不新增第二套视频 Prompt 模板。
3. 不新增第二套图片 provider 调用逻辑。
4. 不新增第二套视频 provider 调用逻辑。
5. 不使用 lipsync 模板和 lipsync provider 模块。
6. 如果优化图片或视频 Prompt，应修改现有 `Reference/05_02/` 模板。

### 10.1 踩坑记录：05_06 被数据库全局 Grok 覆盖

已踩坑：Session 的 `Variables.default_video_config` 是 Max SD 2，但前端 `prompt-only` 请求未携带模型，且 Session 没有 `VideoAPISettings.json` / `VideosAgentSettings.json`。旧后端因此读取数据库全局 active 视频模型 `xai / grok-imagine-video`，作为 `--video-provider/--video-model` 传给 `05_06`；`05_06` 最终错误复制 `Video_Grok.md`，生成的业务 Prompt 也记录为 `video_grok`。

永久约束：

1. `05_06` 的执行模型唯一业务来源是 `SessionContext/Variables.json` 的 `default_video_config`；前置图片/TTS 同理读取对应 `default_*_config`。
2. 不得以数据库 active 项、请求 payload、`VideoAPISettings.json`、`VideosAgentSettings.json` 或 `talking_head.video_model` 覆盖 Variables。
3. 数据库只允许按 Variables 已选 provider/model 读取 API key 到内存。
4. `prompt-only` 每次必须重新执行 `default_video_config -> video_module_for() -> 模块模板`；不得根据旧 Prompt 元数据沿用旧 provider。
5. Max SD 2 必须复制并渲染 `Video_OpenRouter.md`，最终 JSON 必须记录 `provider_profile=video_openrouter`、`template_source=Ref_05_02_Video_OpenRouter.md`。
6. 后端不得向 `05_06` 注入数据库全局 `--video-provider/--video-model`；非空 CLI 参数与 Variables 不一致时必须阻断。
7. 顶部首次执行 `prompt-only` 仍使用 Analysis_V1 `05_06` 的通用 provider 模板；只有 Video Prompt 编辑器的“重新加载”按 workflow 分流，`person_talking_head_v1` 转入 TalkingHead_V1 `05_02 + Video_SDR2V_TalkingHead.md`，普通 Analysis 工作流仍留在 `05_06`。

## 11. 执行顺序

### 11.1 05_05 计划阶段

1. 读取 `SessionContext/Variables.json`。
2. 读取当前已保存 StoryBoard。
3. 使用 `05_01` 的目标范围规则：`scene / shot / task`。
4. 使用 `05_01` 的首帧来源规则。
5. 使用 `05_01` 的切段和尾帧依赖规则。
6. 移除 Sync / Lipsync 步骤。
7. 输出 `video_only_generation_plan.json`。

### 11.2 05_06 执行阶段

每个 segment 的顺序：

1. 校验 StoryBoard Working 中是否已有 Final。
2. 如果已有 Final，跳过当前 segment。
3. 执行或确认 Audio。
4. 执行或确认 First Frame。
5. 生成 Video Prompt。
6. 调用视频模型生成 Raw。
7. 发布 Raw 到 StoryBoard Working。
8. 抽取尾帧并发布到 StoryBoard Working。
9. 等待用户是否确认 Raw 为 Final。

### 11.2.1 05_06 三种执行模式

`05_06` 与 `05_04` 的 ImagePlan 控制方式保持一致，支持三种模式：

```text
prompt-only
video-only
prompt-and-video
```

模式规则：

1. `prompt-only`：只生成或刷新 `SessionOutput/storyboard/Working/{asset_key}_VideoPrompt.json`，不调用视频模型。
2. `video-only`：读取当前业务 Video Prompt 生成 Raw Video；如果 Prompt 缺失则 blocked。
3. `prompt-and-video`：同一次执行中先生成 Prompt，再读取该 Prompt 生成 Raw Video。
4. `prompt-only` 不要求视频 provider API key 可用。
5. `video-only` 和 `prompt-and-video` 需要视频 provider/model 配置和真实 API key 可解析。
6. `prompt-and-video` 表示用户选择全自动执行：生成 Prompt 后立即生成 Video，中间不要求人工确认。
7. 人工可控流程是：`prompt-only -> 用户界面修改 Prompt -> video-only -> 手动 Confirm Final`。
8. Prompt 没有 `edited` / `stale` 等派生状态；人工修改 Prompt 文件后，Prompt 仍是 `completed_working`。
9. 如果已经执行过 `video-only` 或 `prompt-and-video` 并生成 Raw，则不能再次执行 `prompt-only`、`video-only` 或 `prompt-and-video`；必须先通过受控清理 / 重新生成流程处理 Raw，才允许重新开始。
10. 如果已经 Confirm Final，则不能再次执行 `prompt-only`、`video-only` 或 `prompt-and-video`；必须先清除 Final / Raw，才允许重新开始。

### 11.3 确认 Final 阶段

用户点击“确认是最终视频”后：

1. 校验 Raw 或已有 Final 至少一个存在且非空。
2. 如果需要用 Raw 覆盖 Final，覆盖前按 StoryBoard assets history 规则备份旧 Final。
3. 如果 Final 缺失，拷贝 Raw 为 Final；如果 Final 已存在但未绑定，只补绑定当前 Final，不重新生成 Raw。
4. 从当前 Final 抽取 / 刷新 `{asset_key}_TailFrame.png`；覆盖旧 TailFrame 前按 StoryBoard assets history 规则备份。
5. 同步 `srt_storyboard.json` 视频绑定。
6. 同步 `koubo_storyboard_edit.json` 视频绑定。
7. 只有 Final、TailFrame 和两个 StoryBoard JSON 绑定都成功后，Confirm Final 才能显示 completed。
8. 重新计算 artifact status。

## 12. 参数建议

### 12.1 05_05 参数

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

### 12.2 05_06 参数

```text
--workspace <workspace>
--database-url <database_url>
--mode prompt-only|video-only|prompt-and-video
--max-segments 0
--execute-audio
--execute-image
--execute-video
--image-provider <provider>
--image-model <model>
--video-provider <provider>
--video-model <model>
--tts-provider <provider>
--tts-model <model>
--provider-timeout-seconds 1800
--execution-job-id <job_id>
--source-plan-hash <hash>
--target-segment-id <segment_id>
--target-asset-key <asset_key>
--overwrite-prompt
--overwrite-video
--force
--resume
--print-json
```

不提供：

```text
--execute-lipsync
--lipsync-provider
--lipsync-model
--execute-audio-video-sync
```

## 13. 状态与展示

Video Only 的前端状态只展示：

```text
Audio
First Frame
Prompt
Video
Confirm Final
```

状态语义：

1. Audio 绿色：SegmentAudio 已在 StoryBoard Working。
2. First Frame 绿色：新图或可用首帧已在 StoryBoard Working。
3. Prompt 绿色：VideoPrompt 已在 StoryBoard Working。
4. Video 绿色：Raw 或 Final 已在 StoryBoard Working。
5. Confirm Final 绿色：Final 文件存在、StoryBoard JSON 已绑定，且同 key `TailFrame.png` 已从当前 Final 抽取成功。
6. Confirm Final 待确认：Raw 存在但 Final 不存在或未绑定；或 Final 已存在但 TailFrame 缺失 / 过期，需要后端补抽。
7. Confirm Final 灰色：Raw 和可补绑定的 Final 都不存在，不能确认。
8. 错误态：模型调用、文件发布或 StoryBoard 同步失败。
9. Prompt 只有单项完成状态；业务 Prompt 文件被人工编辑后仍保持 `completed_working`，不新增 edited / stale / dirty 状态。

## 14. 状态文件与踩坑规则

### 14.1 唯一执行状态文件

Video Only 只能有一套执行状态文件：

```text
SessionOutput/storyboard/video_only_plan_execution_state.json
```

工具本地可以写同内容快照：

```text
S13_05_06_VideoOnlyPlanExecutor/Output/video_only_plan_execution_state.json
```

但页面端只能以 `SessionOutput/storyboard/video_only_plan_execution_state.json` 为运行态来源。

不得新增或恢复以下伪状态源：

```text
video_only_plan.ui_execution_state.json
ui_execution_state
frontend_local_execution_state
```

规则：

1. `artifact_status` 只能作为后端实时扫描 StoryBoard Working 的即时快照，用于判断 Prompt / Raw / Final 文件是否存在。
2. `artifact_status` 不是执行状态文件，不能替代 `video_only_plan_execution_state.json`。
3. 页面展示当前运行中的黄色状态时，execution state 优先级高于 artifact status。
4. 文件已经存在但本轮 step 仍是 `running_generate` 时，页面必须继续显示黄色运行态，不能提前变绿。
5. execution state / result 的 `source_plan_hash` 必须与当前 `video_only_generation_plan.plan_hash` 一致；不一致时旧状态只能作为历史查看，不能染色当前 plan。

### 14.2 Step 状态推进

`05_06` 启动执行后，后端必须立即写入具体 segment 绑定：

```json
{
  "current_segment_id": "shot_001_scene_001_segment_001",
  "current_asset_key": "srt_0001_01",
  "current_step": "prompt",
  "current_step_status": "running_generate",
  "segments": {
    "srt_0001_01": {
      "segment_id": "shot_001_scene_001_segment_001",
      "asset_key": "srt_0001_01",
      "steps": {
        "prompt": {
          "status": "running_generate"
        }
      }
    }
  }
}
```

规则：

1. 不能只写整体 `status=queued/running`，否则 UI 找不到具体 Segment 的黄色运行态。
2. `05_06` 启动后不得清空 API 初始写入的 `current_segment_id/current_asset_key/current_step/current_step_status`。
3. `prompt-and-video` 必须按同一次执行顺序推进：

```text
Prompt running_generate
Prompt completed_working
Video running_generate
Video completed_working
```

4. 不能在初始排队时因为 Video 依赖 Prompt 尚未生成，就把 Video step 从 selected tasks 中过滤掉。
5. Video 生成耗时较长，不能因为顶部按钮从 disabled 变回 enabled 或整体 job 状态结束，就推断 Video step 已完成。
6. Video step 必须等 `segments[asset_key].steps.video.status=completed_working`。
7. 前端不能用 `mode=prompt-and-video` 或整体 `execution_state.status=running` 猜测哪一步正在运行。
8. UI 必须读取具体 step 状态：`segments[asset_key].steps.prompt.status` 和 `segments[asset_key].steps.video.status`。

### 14.3 Prompt 状态踩坑

Prompt 是唯一业务版本：

```text
SessionOutput/storyboard/Working/{asset_key}_VideoPrompt.json
```

规则：

1. `prompt-only` 生成或覆盖当前业务 Prompt。
2. 页面编辑 Prompt 时，也写回同一个业务 Prompt。
3. 页面编辑 Prompt 后，Prompt step 仍保持 `completed_working`；不新增 `edited`、`dirty`、`stale_prompt` 等状态。
4. `video-only` 读取同一个业务 Prompt。
5. 不允许创建 `SystemVideoPrompt`、`EditedVideoPrompt`、`UserVideoPrompt` 这类并列业务版本。
6. 覆盖已有 Prompt 前必须按 StoryBoard assets history 规则备份。
7. `Prompt` 按钮成功只代表 Prompt Step 完成，不能把 Video Step 染绿。
8. `Prompt + Video` 中 Prompt 成功、Video 失败时，Prompt 保持绿色，Video 显示错误，不能把 Prompt 回退。
9. Raw 或 Final 存在时，Prompt Step 已被下游产物消费，不能再次运行 Prompt；只有清除 Raw 后才允许重新运行 Prompt。

### 14.4 Video 状态踩坑

Video 有 Raw 和 Final 两种业务文件：

```text
SessionOutput/storyboard/Working/{asset_key}_Video_Raw.mp4
SessionOutput/storyboard/Working/{asset_key}_Video_Final.mp4
```

规则：

1. `video-only` 或 `prompt-and-video` 成功后，Video Step 绿色表示 Raw 已进入 StoryBoard Working。
2. Raw 绿色不等于 Final 绿色。
3. Confirm Final 只有在 Final 文件存在、StoryBoard JSON 已绑定且同 key `TailFrame.png` 已从当前 Final 抽取成功时才绿色。
4. 如果 Raw 已生成，但 Confirm Final 失败，Video Step 保持绿色，Confirm Final 显示错误。
5. 如果删除旧 Raw 后重新执行 `video-only` 或 `prompt-and-video`，必须先把 Video step 写成 `running_generate`；发布成功后再写 `completed_working`。
6. 如果 `artifact_status.raw_in_working=true`，但本轮 Video step 仍是 `running_generate`，运行态优先显示黄色。
7. 不得仅因为 `S13_05_06_VideoOnlyPlanExecutor/Output/` 中有视频，就把页面 Video Step 标记为完成。
8. 页面 Video 完成必须来自 StoryBoard Working 中 Raw 或 Final 文件存在，并且执行状态或 artifact status 属于当前 plan。
9. Raw 存在时，Video step 已完成但未 Final；此时 `Prompt`、`Video`、`Prompt + Video` 全部禁止普通重跑，只能走 `Confirm Final` 或受控清理 / 重新生成 Raw 流程。
10. Final 存在、StoryBoard JSON 已绑定且同 key `TailFrame.png` 已从当前 Final 抽取成功时，Video step 与 Confirm Final 均完成；Prompt / Video / Prompt + Video 禁止重跑，除非用户先清除 Final / Raw。
11. 对 `05_02` 而言，Raw 发布成功就是 Video Step 完成；后续 Lip Sync 或空镜 audio_replace_retime 都属于 Sync Step。
12. Sync Step 失败时，页面不能把 Video Step 改成失败；应显示 Video 已有 Raw、Sync 失败或待重试。
13. 用户在 StoryBoard 界面删除某个 Dialogue 的视频并保存，语义是“当前视频结果不满意，需要重新生成”，不是仅取消 Final 绑定。
14. 删除 StoryBoard 视频时必须复用现有 history 机制，将同一 `{asset_key}` 下的 `{asset_key}_Video_Final.*`、`{asset_key}_Video_Raw.*` 和 `{asset_key}_TailFrame.png` 从 `SessionOutput/storyboard/Working/` 移入 `SessionOutput/storyboard/assets/history/`；不得新增任何状态文件或伪状态源。
15. 删除 StoryBoard 视频后必须清空 `srt_storyboard.json` 和 `koubo_storyboard_edit.json` 中对应 Dialogue 的 Final 视频绑定，并修正现有 `SessionOutput/storyboard/video_only_plan_execution_state.json` 中该 `{asset_key}` 的 `video` / `confirm_final` 完成态，避免页面继续把 Video 或 Confirm Final 染绿。
16. 删除视频不清理 `{asset_key}_VideoPrompt.json`，Prompt 仍可作为重新生成视频的业务 Prompt 继续使用。
17. StoryBoard 保存成功后，如果 Video Only Plan 弹窗已打开，页面必须调用现有执行状态刷新接口立即刷新；不得依赖等待下一轮轮询。

### 14.5 Confirm Final 状态踩坑

Confirm Final 是手动动作，不属于 `prompt-only / video-only / prompt-and-video` 的自动执行步骤。

规则：

1. Confirm Final 按钮只在 Raw 存在且当前没有执行任务运行时可点击。
2. Confirm Final 点击后必须走后端 API，不允许前端只改本地状态。
3. 后端拷贝 Raw 为 Final 前必须备份旧 Final；已有 Final 只补绑定时也必须校验文件非空。
4. 后端必须从当前 Final 抽取 / 刷新 `TailFrame.png`，覆盖旧 TailFrame 前必须备份。
5. 后端必须同步 `srt_storyboard.json` 和 `koubo_storyboard_edit.json`。
6. 任意一个 StoryBoard JSON 同步失败，或 TailFrame 抽取失败，Confirm Final 不能显示绿色成功。
7. Confirm Final 成功后，应刷新 StoryBoard detail，让主界面视频槽立即读到 Final 绑定。
8. Confirm Final 失败不能清除 Raw，也不能把 Video Step 改回未完成。
9. Confirm Final 成功后，当前 Segment 的尾帧才允许作为后续 Segment 的首帧来源。
10. Confirm Final 成功后，依赖当前尾帧的下一个 Segment 可以继续跑。
11. Raw 未 Confirm Final 时，即使已经抽取 TailFrame，也不能用于驱动下一个 Segment 继续生成。

## 15. 页面端需求

### 15.1 API

建议新增后端接口：

```text
POST /api/koubo-storyboard/tasks/{task_id}/video-only-plan
POST /api/koubo-storyboard/tasks/{task_id}/video-only-plan/execute
GET  /api/koubo-storyboard/tasks/{task_id}/video-only-plan/execution
POST /api/koubo-storyboard/tasks/{task_id}/video-only-plan/segments/{asset_key}/confirm-final
GET  /api/koubo-storyboard/tasks/{task_id}/video-only-plan/prompts/{asset_key}
PUT  /api/koubo-storyboard/tasks/{task_id}/video-only-plan/prompts/{asset_key}
```

接口规则：

1. `execute` 的 body 使用 `mode=prompt-only|video-only|prompt-and-video`。
2. `confirm-final` 负责 Raw -> Final 拷贝、已有 Final 补绑定、`TailFrame.png` 抽取 / 刷新和 StoryBoard 绑定同步。
3. `execution` 返回 plan、execution_state、execution_result、artifact_status、binding_status。
4. 返回的 `binding_status.state_matches_current_plan` 必须用于前端判断旧状态是否能绑定当前 plan。
5. Prompt GET / PUT 只读写唯一业务 Prompt 文件。

### 15.2 Modal

Video Only Modal 顶部展示：

```text
Prompt {done}/{total}
Video {done}/{total}
Final {done}/{total}
```

操作区：

```text
Prompt
Video
Prompt + Video
```

Segment 行展示：

```text
Audio -> First Frame -> Prompt -> Video -> Confirm Final
```

规则：

1. Prompt badge 点击后打开 Video Prompt 编辑器；编辑保存不改变 Prompt step 状态，仍为 `completed_working`。
2. Video badge 点击后打开 Raw / Final 视频查看层。
3. Confirm Final badge 点击后执行确认动作。
4. 执行中 Modal 只读，禁止编辑 Prompt 和确认 Final。
5. 旧 plan 的 execution state / result 可以查看，但不能给当前 plan 染色。
6. 多 Shot 按文档流纵向展示。
7. Raw 或 Final 存在时，顶部 `Prompt`、`Video`、`Prompt + Video` 按钮 disabled，并提示先确认 Final 或通过受控流程清除 / 重新生成 Raw。
8. Raw 未 Confirm Final 时，后续 Segment 的 “use previous tail” 相关状态必须显示 blocked / disabled，而不是继续生成。

### 15.3 与 ImagePlan 踩坑对齐

以下 ImagePlan 踩坑必须在 VideoOnly 中避免：

1. 不能恢复或新增前端本地伪状态源。
2. 不能用 artifact status 替代 execution state。
3. 顶部按钮不能承担具体 step 颜色展示。
4. 后端必须写入具体 segment + step 的 running 状态。
5. 执行器启动时不能清空初始 running marker。
6. `Prompt + Video` 不能因为 Video 依赖 Prompt 就在初始 selection 中过滤 Video。
7. 不能通过整体 job 状态推断具体 Video Step 完成。
8. 不能用旧 plan 的状态染色当前 plan。
9. 不能用工具 Output 文件绕过 StoryBoard Working 判断。
10. 页面组件的 running class 必须随 step status 响应式变化，不能在创建时算死。

## 16. 验收标准

### 16.1 Video Only 生成 Raw

1. 运行 `05_05` 后生成 `SessionOutput/storyboard/video_only_generation_plan.json`。
2. 运行 `05_06` 后生成 `{asset_key}_Video_Raw.mp4`。
3. Raw 文件存在于 `SessionOutput/storyboard/Working/`。
4. Raw 文件同时被记录在 `05_06` execution result 中。
5. 未点击确认 Final 时，不生成或不绑定 `{asset_key}_Video_Final.mp4`。

### 16.2 确认 Final

1. 点击“确认是最终视频”后，Raw 被拷贝为 Final。
2. Final 文件存在于 `SessionOutput/storyboard/Working/`。
3. `srt_storyboard.json` 中对应 Dialogue 的视频绑定指向 Final。
4. `koubo_storyboard_edit.json` 存在时，也同步指向 Final。
5. 再打开 VideoPlan 时，该 Segment 不应被识别为待生成视频。

### 16.3 05_02 接续 Raw 只跑 Sync

1. 已有 `{asset_key}_Video_Raw.mp4` 但没有 Final 时，运行 `05_02` 不重新调用视频模型。
2. `05_02` 把 StoryBoard Raw 拷贝到自己的 `S9` Working。
3. `05_02` 使用该 Raw 和 SegmentAudio 执行 Sync。
4. talking head 场景走 Lip Sync；空镜 / cutaway 场景仍按 `05_02` 原逻辑走 audio_replace_retime / 音画同步。
5. Sync 成功后发布 Final。
6. Final 发布后同步 StoryBoard JSON。
7. execution result 记录本次 Video 使用的是 StoryBoard Raw，而不是本次重新生成。
8. 如果 Sync 失败，Video Step 仍显示 Raw 完成，Sync 显示失败或待重试。

### 16.4 StoryBoard 金标准

1. 删除 `S13_05_06_VideoOnlyPlanExecutor/Working/` 后，只要 StoryBoard Raw 仍存在，`05_02` 仍能接续 Sync。
2. 删除 `S9_05_02_VideoPlanExecutor/Working/` 后，只要 StoryBoard Final 已存在并绑定，前端仍识别最终视频完成。
3. 任何工具目录中的旧文件都不能让前端显示当前 Segment 完成，除非 StoryBoard Working 和 StoryBoard JSON 仍证明该业务文件有效。

### 16.5 Prompt / Video 三按钮

1. 点击 `Prompt` 只生成 Prompt，不调用视频模型。
2. 点击 `Video` 读取当前 Prompt 生成 Raw，不重新生成 Prompt。
3. 点击 `Prompt + Video` 同轮完成 Prompt running/completed，再完成 Video running/completed。
4. Prompt 失败时，Video 不执行并显示 blocked / failed。
5. Video 失败时，Prompt 不回退。
6. 页面不能把顶部按钮恢复 enabled 当成 Video 完成。
7. Raw 存在后再次点击 `Prompt`、`Video` 或 `Prompt + Video` 必须被禁止；清除 Raw 后才能重新开始。
8. 人工修改 Prompt 文件后，Prompt 状态仍是 `completed_working`，点击 `Video` 使用修改后的当前 Prompt。
9. Raw 未 Confirm Final 时，后续 Segment 不得使用该 Raw 的尾帧继续生成。
10. Confirm Final 后，后续 Segment 才能使用该 Final 的尾帧继续生成，并且可以立即继续执行。

## 17. 测试方案

测试目标：

1. 证明 `05_01 / 05_02` 与 `05_05 / 05_06` 都以 StoryBoard 为唯一业务金标准。
2. 证明 Raw / Final 分离正确：Raw 完成 Video，Final 只能由 Sync 或 Confirm Final 产生。
3. 证明页面状态不会复现 `05_03 / 05_04` 的状态漂移问题。
4. 证明 `Prompt / Video / Prompt + Video / Confirm Final` 的按钮语义稳定。

### 17.1 测试环境准备

准备一个最小 StoryBoard 工作区，至少包含：

1. 一个 talking head Segment。
2. 一个空镜 / cutaway Segment。
3. 两个连续 Segment，用于验证前一个 Segment 的 Final 尾帧是否能解锁后一个 Segment。
4. 已保存的 `srt_storyboard.json`。
5. 如页面使用 `koubo_storyboard_edit.json`，也准备对应编辑态文件。
6. 可用 Audio 或可触发 Audio 生成的输入。
7. 可用 First Frame 或可触发新图生成的输入。

测试前清理当前目标 Segment 的业务文件：

```text
SessionOutput/storyboard/Working/{asset_key}_VideoPrompt.json
SessionOutput/storyboard/Working/{asset_key}_Video_Raw.mp4
SessionOutput/storyboard/Working/{asset_key}_Video_Final.mp4
SessionOutput/storyboard/Working/{asset_key}_TailFrame.png
```

清理只针对测试目标 Segment，不删除其它用户资产。

### 17.2 后端计划测试

用例 A：生成 Video Only Plan。

1. 运行 `05_05_VideoOnlyPlanGenerator.py`。
2. 确认生成 `SessionOutput/storyboard/video_only_generation_plan.json`。
3. 确认计划步骤只有 `Audio / First Frame / Prompt / Video`。
4. 确认计划不包含 `Sync / LipSync / audio_replace_retime` 执行步骤。
5. 确认计划读取的是当前已保存 StoryBoard，而不是工具 Working 快照。

用例 B：StoryBoard Final 已存在。

1. 预置 `{asset_key}_Video_Final.mp4` 并绑定到 StoryBoard JSON。
2. 运行 `05_05`。
3. 如果同 key `TailFrame.png` 已存在且来自当前 Final，当前 Segment 应识别为 Final 已完成，不应计划 Raw 生成。
4. 如果 TailFrame 缺失或过期，当前 Segment 不能解除下游依赖，必须提示或触发补抽。
5. 下一个 Segment 只有在 Final 和 TailFrame 都有效时，才可使用该 Final 的尾帧作为首帧来源。

用例 C：StoryBoard 只有 Raw。

1. 预置 `{asset_key}_Video_Raw.mp4`，不绑定 Final。
2. 运行 `05_05`。
3. 当前 Segment 的 Video 可显示 Raw 已存在，但 Final 不完成。
4. 下一个 Segment 不能使用该 Raw 的尾帧继续生成。

### 17.3 05_06 执行测试

用例 D：Prompt-only。

1. 点击或调用 `prompt-only`。
2. 只生成 `{asset_key}_VideoPrompt.json`。
3. 不调用视频模型。
4. 不生成 Raw。
5. Prompt step 为 `completed_working`。
6. Video step 仍为 pending / not completed。

用例 E：手动修改 Prompt 后 Video-only。

1. 执行 `prompt-only`。
2. 手动编辑并保存同一个 `{asset_key}_VideoPrompt.json`。
3. 确认 Prompt 状态仍为 `completed_working`，不出现 `edited / stale / dirty`。
4. 执行 `video-only`。
5. 视频模型读取修改后的当前 Prompt。
6. 生成 `{asset_key}_Video_Raw.mp4`。
7. 不生成或不绑定 `{asset_key}_Video_Final.mp4`。

用例 F：Prompt + Video。

1. 点击或调用 `prompt-and-video`。
2. execution state 先写入 Prompt `running_generate`。
3. Prompt 完成后写入 `completed_working`。
4. 再写入 Video `running_generate`。
5. Raw 发布到 StoryBoard Working 后，Video 写入 `completed_working`。
6. Confirm Final 仍为待确认。

用例 G：Video 失败。

1. 让视频模型调用失败或下载失败。
2. Prompt 已完成时不得回退。
3. Video 显示 failed。
4. 不生成 Raw 时，不能把 Video 显示为完成。
5. 页面不能因为顶部按钮恢复 enabled 而显示 Video 完成。

### 17.4 Confirm Final 测试

用例 H：Raw 确认为 Final。

1. 预置或生成 `{asset_key}_Video_Raw.mp4`。
2. 点击 Confirm Final。
3. 后端拷贝 Raw 为 `{asset_key}_Video_Final.mp4`。
4. 后端从新的 Final 抽取 `{asset_key}_TailFrame.png`。
5. 同步 `srt_storyboard.json` 视频绑定。
6. 同步 `koubo_storyboard_edit.json` 视频绑定。
7. Confirm Final step 显示 completed。
8. 下一个 Segment 从 blocked / waiting 解除，可以继续执行。

用例 I：Confirm Final 失败。

1. 制造 StoryBoard JSON 写入失败、绑定失败或 TailFrame 抽取失败。
2. Confirm Final 显示 failed。
3. Raw 文件不得被清除。
4. Video step 仍保持 Raw completed。
5. Final 不得显示 completed。

用例 J：覆盖已有 Final。

1. 预置旧 `{asset_key}_Video_Final.mp4`。
2. 点击 Confirm Final。
3. 覆盖旧 Final 和旧 TailFrame 前必须写入 StoryBoard asset history 备份记录。
4. 新 Final 绑定成功，且新 `TailFrame.png` 来自新 Final。
5. 旧 Final 和旧 TailFrame 不应无记录丢失。

### 17.5 05_02 接续测试

用例 K：StoryBoard 只有 Raw，talking head。

1. StoryBoard Working 中存在 `{asset_key}_Video_Raw.mp4`，不存在 Final。
2. 运行 `05_02_VideoPlanExecutor.py`。
3. `05_02` 不调用 Video Prompt / Video 模型。
4. `05_02` 把 StoryBoard Raw 拷贝到 `S9_05_02_VideoPlanExecutor/Working/`。
5. Video step 显示 `completed_working`。
6. Sync step 走 Lip Sync。
7. Lip Sync 成功后发布 Final 并同步 StoryBoard JSON。

用例 L：StoryBoard 只有 Raw，空镜 / cutaway。

1. StoryBoard Working 中存在 `{asset_key}_Video_Raw.mp4`，不存在 Final。
2. Segment 被标记为空镜 / cutaway 或不需要 Lip Sync。
3. 运行 `05_02`。
4. `05_02` 不重新生成 Video。
5. Video step 显示 `completed_working`。
6. Sync step 按原逻辑执行 audio_replace_retime / 音画同步。
7. Sync 成功后发布 Final 并同步 StoryBoard JSON。

用例 M：Sync 失败。

1. 让 Lip Sync 或 audio_replace_retime 失败。
2. Video step 仍保持 Raw completed。
3. Sync step 显示 failed。
4. Final 不得绑定。
5. 重试 Sync 时继续复用 StoryBoard Raw。

用例 N：StoryBoard 已有 Final。

1. StoryBoard Working 中存在 Final 且 JSON 已绑定。
2. 运行 `05_02`。
3. 不重新生成 Video。
4. 不执行 Sync。
5. 页面仍显示 Final completed。

### 17.6 StoryBoard 金标准测试

用例 O：删除 `S13 Working`。

1. 生成 Raw 后删除 `S13_05_06_VideoOnlyPlanExecutor/Working/`。
2. 保留 StoryBoard Raw。
3. 页面仍显示 Video Raw completed。
4. `05_02` 仍能接续 Sync。

用例 P：删除 `S9 Working`。

1. `05_02` 已发布 Final 并绑定 StoryBoard。
2. 删除 `S9_05_02_VideoPlanExecutor/Working/`。
3. 页面仍显示 Final completed。
4. 再打开 VideoPlan 时不应被识别为待生成视频。

用例 Q：工具 Output 有旧视频，但 StoryBoard 无业务文件。

1. 只保留 `S13/Output` 或 `S9/Output` 中的旧视频。
2. 删除 StoryBoard Raw / Final。
3. 页面不得显示 Video completed。
4. `05_02` / `05_06` 不得从工具 Output 推断业务完成。

### 17.7 前端状态测试

用例 R：运行中状态。

1. 触发 `prompt-and-video`。
2. 页面读取 `SessionOutput/storyboard/video_only_plan_execution_state.json`。
3. Prompt running 时只让 Prompt step 显示黄色。
4. Video running 时只让 Video step 显示黄色。
5. 顶部按钮只负责 disabled，不显示具体 step 颜色。

用例 S：完成态状态。

1. 执行完成后刷新页面。
2. 页面以 StoryBoard Working + StoryBoard JSON 重新扫描状态。
3. `artifact_status` 只作为即时快照，不写成第二套状态文件。
4. 旧 plan 的 execution state / result 不能给当前 plan 染色。

用例 T：Raw 存在后的按钮状态。

1. Raw 存在，Final 不存在。
2. `Prompt / Video / Prompt + Video` 全部 disabled。
3. Confirm Final 可点击。
4. 页面提示用户先 Confirm Final 或走受控清理 / 重新生成 Raw 流程。

用例 U：Final 存在后的按钮状态。

1. Final 存在且 StoryBoard JSON 已绑定。
2. `Prompt / Video / Prompt + Video / Confirm Final` 均 disabled 或显示已完成不可重复。
3. 只有受控清理流程能重新开始。

### 17.8 回归测试矩阵

必须覆盖以下组合：

| 场景 | Prompt | Raw | Final | StoryBoard 绑定 | 预期页面状态 |
| --- | --- | --- | --- | --- | --- |
| 初始 | 无 | 无 | 无 | 无 | Prompt / Video 可执行，Confirm Final 禁用 |
| Prompt 已生成 | 有 | 无 | 无 | 无 | Prompt completed，Video 可执行 |
| Raw 已生成 | 有 | 有 | 无 | 无 | Video completed，Confirm Final 可执行，三按钮禁用 |
| Final 已确认 | 有 | 有 | 有 | Final 绑定 | Final completed，下一个 Segment 可继续 |
| Raw 有但 Sync 失败 | 有 | 有 | 无 | 无 | Video completed，Sync failed，Final 未完成 |
| 工具 Output 有旧视频 | 任意 | 无 | 无 | 无 | 不得显示 Video completed |

### 17.9 自动化与人工验证边界

建议自动化覆盖：

1. 文件发布路径和命名。
2. execution state step 状态推进。
3. Raw / Final / StoryBoard JSON 绑定判断。
4. `05_02` Raw 接续路径。
5. Prompt 修改后状态不变。
6. Raw 存在后三按钮 disabled 的 API payload。

建议人工或端到端 UI 覆盖：

1. Modal 中多 Shot 纵向展示。
2. Prompt / Video / Confirm Final step 颜色。
3. 执行中按钮 disabled。
4. Confirm Final 后主 StoryBoard 视频槽刷新。
5. Raw 未确认时后续 Segment blocked，Confirm Final 后立即解除。

## 18. 待实现清单

1. 新增 `05_05_VideoOnlyPlanGenerator.py`。
2. 新增 `05_06_VideoOnlyPlanExecutor.py`。
3. 更新 `tool_registry.json`，注册 `05_05` 和 `05_06`。
4. 新增后端常量和执行路由。
5. 新增 Video Only 前端入口和 Modal。
6. 为 Video Only 增加 `Prompt / Video / Prompt + Video` 三按钮。
7. 为 Video 步骤增加“确认是最终视频”动作。
8. 新增唯一 execution state 文件，禁止前端伪状态源。
9. 修改 `05_02`，在 Video 生成前检测 StoryBoard Raw / Final。
10. 修改 `05_02`，生成 Raw 后同步 Raw 到 StoryBoard Working。
11. 增加回归测试：Raw 跨工具接续、Final 确认、StoryBoard 金标准、Prompt/Video step 状态推进。

## 19. 05_06 Max SD 2 非空镜口播接通要求（2026-07-14）

1. `05_06` 必须复用 `05_02` 的 `video_selection_for_segment()`、模板选择和参考视频准备函数，不得另写一套模型推断。
2. 模型只读取 `Variables.default_video_config`。当值为 `openrouter / bytedance/seedance-2.0` 且 Segment 非空镜、非 DanceMimic 时，Prompt 必须来自 `Video_SDR2V.md`。
3. 显式 Segment 参考视频优先；没有显式参考视频时，把 `Reference/05_02/Video_SDR2V.mp4` 复制到 `S13_05_06_VideoOnlyPlanExecutor/Working/Video_SDR2V.mp4` 并作为兜底参考。
   - 固定兜底视频最大允许 `15.0s`；标准素材必须通过回归测试验证，执行目录不得继续使用历史的 `28.96s` 副本。
4. Video 执行时，首帧图片和最终解析出的参考视频必须一起进入 OpenRouter `input_references`。只复制文件、不传 `reference_videos` 属于未接通。
5. 空镜继续使用 `Video_OpenRouter.md` 且不注入 SDR2V 兜底视频；DanceMimic 继续使用它自己的模板与分段视频，优先级高于普通 Max SD 2 口播。
6. Prompt、Video、Prompt + Video 三种按钮路径必须得到相同路由结果；审计文件需包含最终 `reference_video(s)`、`reference_mode`、`video_generation_mode` 和 `reference_video_role`。
7. `person_talking_head_v1` 使用 Max SD 2 且目标图隐私网格开启时，`05_06` 必须从 StoryBoard 根部 `talking_head_config.max_sd_2_reference` 恢复隐私上下文；上一段尾帧物化为下一段 `Image_New`、已有首帧和新生成首帧都必须先经过 TalkingHead 本地连续帧隐私函数。

### 19.1 Max SD 2 口播隐私网格严格门禁

Analysis_V1 `05_02 / 05_06` 的新图与尾帧连续性隐私处理必须复用同一判断函数，并且仅在以下条件全部为真时执行：

1. Session Variables 默认视频模型严格等于 `openrouter / bytedance/seedance-2.0`；
2. Segment 同时具有 `need_lipsync=true` 与 `sync_mode=lipsync`；
3. Segment 不是 cutaway、产品特写、无可见人脸或 DanceMimic；
4. `reference_privacy` 已启用且模式为 `red_grid_guide`；
5. 目标人物图隐私开关开启；
6. StoryBoard / Segment 中的 Max SD 2 隐私资产状态确认目标图网格已生成。

其它模型、空镜、产品镜头、口播标记缺失、目标图开关关闭或隐私资产状态不完整时，必须原样返回输入图片，不运行网格工具。后端 `materialize-tail-frame` 只能复用同一门禁，禁止仅凭 StoryBoard 根部存在历史 `max_sd_2_reference` 就给尾帧加网格。

### 踩坑记录

- 不得从数据库中的 TalkingHead 字段推断 Analysis_V1 模型；数据库只补密钥和连接参数。
- `video_openrouter.py` 默认仍是通用模板，因此 `05_06 build_video_prompt()` 必须把 `video_selection.prompt_template` 传入 Prompt context。
- Analysis_V1 的模板是 `Video_SDR2V.md`；TalkingHead_V1 的模板是 `Video_SDR2V_TalkingHead.md`，重新加载时必须按工具边界区分。
- StoryBoard 根部的 `max_sd_2_reference` 会跨 Segment 保留；若只检查这个字段，非 Max SD 2、空镜和产品镜头也会被错误加网格。执行前必须先校验 Variables 当前模型与 Segment 的 canonical 口播标记。

## 20. 05_06 OpenRouter 参考视频的 R2 传输要求（2026-07-14）

1. `05_06` 必须复用 `05_02 -> video_openrouter.py` 的 R2 运行时配置解析；完整配置存在时，Max SD 2 的显式参考视频和 `Video_SDR2V.mp4` 兜底视频都必须上传 R2，并使用预签名 URL 调用 OpenRouter。
2. provider config 与 ModelCall 审计只记录非敏感的 `public_asset_provider=r2`、endpoint、bucket、region、prefix、TTL 和配置来源，不得持久化 R2 Access Key 或 Secret。
3. 修改 `~/.opencrew/public_assets_r2.env` 后必须重启后端；仅在控制台创建 bucket 或更新文件，不会改变已运行进程的环境。
4. 回归测试必须覆盖：环境配置注入、R2 上传与签名、`input_references[].video_url`、敏感字段不落盘，以及显式非 R2 provider 不被覆盖。

### R2 踩坑记录

- 不能只检查 bucket 是否存在；必须通过 ModelCall 审计和预签名 URL 实际 GET/Range 验证来确认请求链路确实走 R2。
- 完整 R2 配置存在时仍出现 tmpfiles URL，属于接入失败，而不是 OpenRouter 模型随机失败。
