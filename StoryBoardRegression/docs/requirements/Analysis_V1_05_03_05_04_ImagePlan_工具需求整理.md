# Analysis_V1 05_03_ImagePlanGenerator / 05_04_ImagePlanExecutor 工具需求整理

版本：v0.1

状态：需求确认稿。本文用于确认新增图像专用计划与执行工具的边界、目录合同、输入输出、界面控制方式和测试验收标准。本文不要求修改现有 `05_01_VideoPlanGenerator` 和 `05_02_VideoPlanExecutor` 的工具名称。

## 1. 背景

当前 `05_01_VideoPlanGenerator` 已经能根据 StoryBoard 范围生成视频生成计划：

```text
SessionOutput/storyboard/video_generation_plan.json
```

当前 `05_02_VideoPlanExecutor` 是综合执行器，内部包含音频、图片、视频、对嘴型等多类执行逻辑。随着 StoryBoard 页面进入更细粒度的可视化控制，图像生成需要被单独拆出来，形成一个能被用户先确认、再执行的图像专用链路。

新的目标不是替代 `05_01` 或改名 `05_02`，而是在现有视频链路之后增加两个图像专用工具：

```text
05_03_ImagePlanGenerator
05_04_ImagePlanExecutor
```

其中：

1. `05_03_ImagePlanGenerator` 只负责根据界面当前图像状态生成图片任务计划。
2. `05_04_ImagePlanExecutor` 负责生成图片提示词和执行图片生成。
3. 每张图片任务固定包含两个可分离步骤：生成 Prompt、根据 Prompt 生成 Image。
4. 用户可以先生成所有图片 Prompt，在界面确认或修改后，再批量生成图片。
5. 系统生成的 Prompt 和用户修改后的 Prompt 使用同一个业务文件，不区分系统版和人工版。

## 2. 现有工具名称与新增工具顺序

硬性确认：

1. 不修改现有 `05_01_VideoPlanGenerator` 的名称。
2. 不修改现有 `05_02_VideoPlanExecutor` 的名称。
3. 新增图像计划工具命名为 `05_03_ImagePlanGenerator.py`。
4. 新增图像执行工具命名为 `05_04_ImagePlanExecutor.py`。

推荐 Analysis_V1 工具编号和可选执行组：

```text
S1_00_PrepareSessionVariables     <- 00_PrepareSessionVariables.py
S2_01_VideoProbeMetadata          <- 01_VideoProbeMetadata.py
S3_02_01_AudioASR                 <- 02_01_AudioASR.py
S4_02_02_VideoSRTFrame            <- 02_02_VideoSRTFrame.py
S5_03_01_TTSBuilderG              <- 03_01_TTSBuilderG.py
S6_04_01_SRTRewrite               <- 04_01_SRTRewrite.py
S7_04_02_StoryBoard               <- 04_02_StoryBoard.py

视频完整执行组:
  S8_05_01_VideoPlanGenerator     <- 05_01_VideoPlanGenerator.py
  S9_05_02_VideoPlanExecutor      <- 05_02_VideoPlanExecutor.py

图像可控子流程执行组:
  S10_05_03_ImagePlanGenerator    <- 05_03_ImagePlanGenerator.py
  S11_05_04_ImagePlanExecutor     <- 05_04_ImagePlanExecutor.py

后续拼接:
  S12_06_01_VideoPlanComposer     <- 06_01_VideoPlanComposer.py
```

说明：

1. `S10` / `S11` 是当前推荐顺序，不是工具名的一部分。
2. 如果未来 Plan Runner 调整执行顺序，仍遵守 `S{step_index}_{tool_name}` 目录规则。
3. `05_03` / `05_04` 是工具库编号，不要求与 `S` 编号一致。
4. `05_01 + 05_02` 是视频完整执行组。
5. `05_03 + 05_04` 是图像可控子流程执行组。
6. 两个执行组是 OR 关系：用户可以走视频完整执行组，也可以先走图像可控子流程执行组。
7. 如果用户先执行 `05_03 + 05_04` 并改变了 StoryBoard / Working 状态，后续仍可以完整运行 `05_01 + 05_02`，用视频完整执行组重新生成计划并保障最终状态一致。
8. 工具编号不表示必须先执行 `05_02` 再执行 `05_03`；实际运行顺序由界面和 Plan Runner 根据用户选择决定。

## 3. 核心边界

### 3.1 `05_03_ImagePlanGenerator` 边界

`05_03_ImagePlanGenerator` 是计划工具，不是 Prompt 工具，也不是模型执行工具。

它必须：

1. 读取当前 StoryBoard / 界面编辑态中的图像落位情况。
2. 判断哪些 Dialogue / Segment 需要图像生成任务。
3. 为每张待生成图片建立一个 image task。
4. 在每个 image task 中描述两个步骤：
   - `prompt`：生成或准备图片提示词。
   - `image`：根据提示词生成图片。
5. 输出 `image_generation_plan.json`。
6. 保持局部 blocked 继续向后。
7. 不调用图片模型。
8. 不生成图片提示词。
9. 不创建业务 Prompt。
10. 不修改 `srt_storyboard.json` 的 Shot / Scene / Dialogue 结构。

它不得：

1. 调用 LLM / VLM / 图片模型。
2. 写入 `SessionOutput/storyboard/Working/{asset_key}_ImagePrompt.json`。
3. 写入最终图片。
4. 调用 `image_gpt.py` / `image_gemini.py` / `image_grok.py` 中的 Prompt 拼接逻辑。
5. 重写或改变原有字幕、Dialogue 文案、时间轴。

### 3.2 `05_04_ImagePlanExecutor` 边界

`05_04_ImagePlanExecutor` 是图像执行器。

它负责：

1. 读取 `image_generation_plan.json`。
2. 按参数选择执行 Prompt 步骤、Image 步骤，或两者都执行。
3. 使用与现有 `05_02_VideoPlanExecutor` 图片子流程完全一致的图片模型 Python 模块生成 Prompt。
4. 使用与现有 `05_02_VideoPlanExecutor` 图片子流程完全一致的图片模型 Python 模块调用图片模型。
5. 把系统生成或人工修改后的当前 Prompt 放在同一个业务路径。
6. 根据当前业务 Prompt 生成图片。
7. 将最终图片发布到 `SessionOutput/storyboard/Working/`。
8. 同步绑定 `srt_storyboard.json` 和 `koubo_storyboard_edit.json`。
9. 生成执行状态和审计结果。

它不得：

1. 自己重新拆 StoryBoard 图像任务。
2. 自己重新推断需要生成哪些图片。
3. 新建第二套图片 Prompt 模板。
4. 新建第二套图片 provider Python 调用逻辑。
5. 在代码里隐藏拼接 Prompt。
6. 把 API key、Authorization header、cookie、数据库连接串写入任何输出文件。

### 3.3 与现有 Video 执行器图片子流程严格一致

本设计的本质不是新增一套图片 Prompt 生成逻辑，而是把现有 Video 执行链路中的图片子流程拆成一个可由界面控制的独立子流程。

硬性原则：

1. `05_04_ImagePlanExecutor` 的 Prompt 生成逻辑必须与现有 `05_02_VideoPlanExecutor` 中图片 Prompt 生成逻辑严格一致。
2. `05_04_ImagePlanExecutor` 使用的 Prompt 模板必须与现有 `05_02_VideoPlanExecutor` 图片子流程使用的模板严格一致。
3. `05_04_ImagePlanExecutor` 使用的图片 provider Python 模块必须与现有 `05_02_VideoPlanExecutor` 图片子流程使用的模块严格一致。
4. 两者在同一个 image task、同一个 StoryBoard、同一个 source image、同一个人物参考、同一个产品参考、同一个 provider/model 下，渲染出的 Prompt package 应保持一致。
5. 拆分后唯一增加的是执行控制点：可以先只生成 Prompt、人工修改 Prompt、再根据当前 Prompt 生成图片。
6. 不允许因为新增 `05_04` 而出现两套模板、两套字段抽取、两套 reference priority、两套 cutaway 判断或两套 provider 请求格式。

换句话说：

```text
原 05_02 图片子流程:
  生成 Image Prompt -> 调图片模型生成 Image

新增可控子流程:
  05_03 只计划哪些图片需要这两个步骤
  05_04 可选择只执行 Prompt
  用户可编辑同一个 Prompt 文件
  05_04 可选择根据当前 Prompt 执行 Image
```

拆分的是流程控制和界面确认点，不是提示词算法、模板系统或图片模型调用系统。

## 4. 图片任务的两步结构

每一张图片任务都包含两个可分离步骤。

```text
Image Task
  -> Prompt Step
  -> Image Step
```

### 4.1 Prompt Step

Prompt Step 由 `05_04_ImagePlanExecutor` 执行。

职责：

1. 使用与 `05_02_VideoPlanExecutor` 图片子流程同源同逻辑的 Prompt package 生成方式，根据 image task、StoryBoard、当前 Dialogue、参考图、人物一致性参考、产品一致性参考、模型模板生成图片 Prompt。
2. 将业务 Prompt 写入：

```text
SessionOutput/storyboard/Working/{asset_key}_ImagePrompt.json
```

3. 将执行快照写入本工具 `Prompt/` 目录。
4. 允许用户在界面修改同一个业务 Prompt 文件。

### 4.2 Image Step

Image Step 由 `05_04_ImagePlanExecutor` 执行。

职责：

1. 读取当前有效业务 Prompt：

```text
SessionOutput/storyboard/Working/{asset_key}_ImagePrompt.json
```

2. 读取 image task 中记录的参考图、人物参考、产品参考。
3. 调用选定图片模型。
4. 输出最终图片到本工具 `Output/`。
5. 发布最终图片到：

```text
SessionOutput/storyboard/Working/{asset_key}_Image_01.png
```

6. 更新 StoryBoard JSON 中对应 Dialogue 的图片绑定。

## 5. Prompt 唯一业务版本规则

系统生成的 Prompt 和用户修改后的 Prompt 是同一个业务版本。

业务 Prompt 唯一入口：

```text
SessionOutput/storyboard/Working/{asset_key}_ImagePrompt.json
```

不得创建以下并列业务版本：

```text
SessionOutput/storyboard/Working/{asset_key}_SystemImagePrompt.json
SessionOutput/storyboard/Working/{asset_key}_EditedImagePrompt.json
SessionOutput/storyboard/Working/{asset_key}_UserImagePrompt.json
```

规则：

1. `05_04_ImagePlanExecutor --execute-prompt` 生成或覆盖当前业务 Prompt。
2. 前端修改 Prompt 时，也修改同一个业务 Prompt 文件。
3. `05_04_ImagePlanExecutor --execute-image` 读取这个当前业务 Prompt 文件生成图片。
4. 工具本地 `Prompt/` 可以保存快照、审计、请求和响应，但不作为页面业务读取入口。
5. 如果覆盖已有业务 Prompt，必须按 StoryBoard assets history 规则备份旧文件。

推荐业务 Prompt 字段：

```json
{
  "schema_version": "analysis_v1_image_prompt_0.1",
  "asset_key": "srt_0001_01",
  "image_task_id": "shot_001_scene_001_srt_0001_01_image",
  "segment_id": "shot_001_scene_001_segment_001",
  "prompt_status": "draft_generated",
  "prompt_origin": "system_generated",
  "prompt_revision": 1,
  "source_plan_hash": "sha256...",
  "source_storyboard_hash": "sha256...",
  "source_image_hash": "sha256...",
  "host_reference_hash": "sha256...",
  "product_reference_hash": "sha256...",
  "provider_profile": "image_gpt",
  "template_source": "Ref_05_02_Image_GPT.md",
  "template_blocks": [],
  "positive_prompt": "",
  "negative_prompt": "",
  "prompt": "",
  "reference_images": [],
  "reference_priority": {},
  "updated_at": ""
}
```

当用户修改后，仍写回同一个文件，可更新：

```json
{
  "prompt_status": "edited",
  "prompt_origin": "user_edited",
  "prompt_revision": 2,
  "updated_at": ""
}
```

## 6. 唯一模板、唯一图片模型 Python 与一致性合同

图片 Prompt 模板、Prompt package 生成逻辑和图片模型执行逻辑必须保持唯一来源，并与现有 `05_02_VideoPlanExecutor` 图片子流程严格一致。

模板唯一来源：

```text
OpenCrew/ToolLibrary/Analysis_V1/Reference/05_02/Image_GPT.md
OpenCrew/ToolLibrary/Analysis_V1/Reference/05_02/Image_Gemini.md
OpenCrew/ToolLibrary/Analysis_V1/Reference/05_02/Image_Grok.md
```

Python 模块唯一来源：

```text
OpenCrew/ToolLibrary/Analysis_V1/video_plan_executor_modules/image_gpt.py
OpenCrew/ToolLibrary/Analysis_V1/video_plan_executor_modules/image_gemini.py
OpenCrew/ToolLibrary/Analysis_V1/video_plan_executor_modules/image_grok.py
```

`05_04_ImagePlanExecutor` 必须复用以上模块。

规则：

1. 不在 `05_04_ImagePlanExecutor.py` 中复制图片 Prompt 拼接逻辑。
2. 不在 `05_04_ImagePlanExecutor.py` 中复制 provider 请求逻辑。
3. 不新增 `image_plan_executor_modules/` 里的重复图片模块，除非未来明确迁移唯一来源。
4. 如果要优化图片 Prompt，修改 `Reference/05_02/Image_*.md`。
5. 如果要优化图片模型调用，修改 `video_plan_executor_modules/image_*.py`。
6. `05_02_VideoPlanExecutor` 和 `05_04_ImagePlanExecutor` 应共享同一套图片模块。
7. `05_04` 不能把 `05_02` 的图片 Prompt 生成逻辑重新实现为另一套“等价逻辑”；必须调用同一套模块入口或抽取同一套共享函数。
8. 如果为了复用需要调整模块入口，应以不改变 Prompt 输出为前提，让 `05_02` 和 `05_04` 同时调用同一个入口。
9. 回归测试必须验证同一输入下 `05_02` 图片子流程与 `05_04` Prompt Step 的 `PromptRendered_{asset_key}_ImagePrompt.json` 内容一致。

## 7. `05_03_ImagePlanGenerator` 输入

必需输入：

```text
SessionContext/Variables.json
SessionOutput/storyboard/srt_storyboard.json
```

优先输入：

```text
SessionOutput/storyboard/koubo_storyboard_edit.json
```

可选输入：

```text
SessionOutput/storyboard/video_generation_plan.json
SessionOutput/storyboard/Working/
SessionOutput/storyboard/assets/images/
SessionOutput/storyboard/assets/videos/
SessionOutput/visual/srt_frames/
SessionOutput/storyboard/consistency_references/
SessionContext/Consistency/
```

输入优先级：

1. `05_01_VideoPlanGenerator` 和 `05_03_ImagePlanGenerator` 必须读取同一个“界面保存后的稳定 StoryBoard 状态”。
2. Plan 生成必须发生在 StoryBoard 主界面保存完成之后；未保存的界面草稿不进入 `05_01` 或 `05_03`。
3. 如果 `koubo_storyboard_edit.json` 是界面保存后的当前主编辑态，则 `05_01` 和 `05_03` 都必须以它为同一输入源。
4. 如果不存在 `koubo_storyboard_edit.json`，则 `05_01` 和 `05_03` 都使用 `srt_storyboard.json`。
5. `05_03` 是 `05_01` 图像相关计划的子集，不允许与 `05_01` 看到的 StoryBoard 内容发生冲突。
6. 如果两个工具的输入 StoryBoard hash 不一致，必须 blocked，而不是继续生成互相冲突的计划。
7. `SessionOutput/storyboard/Working/` 是最终业务图片、Prompt、视频绑定的页面读取目录。
8. `assets/images/` 是上传或备份图片池，不等同于新图槽位。
9. `SessionOutput/visual/srt_frames/` 是原始参考帧来源。

prepare 阶段复制到本工具：

```text
S10_05_03_ImagePlanGenerator/Working/InputFrom_0_Variables.json
S10_05_03_ImagePlanGenerator/Working/InputFrom_7_srt_storyboard.json
S10_05_03_ImagePlanGenerator/Working/InputFrom_7_koubo_storyboard_edit.json
S10_05_03_ImagePlanGenerator/Working/InputFrom_8_video_generation_plan.json
S10_05_03_ImagePlanGenerator/Working/InputParams_image_generation_plan.json
S10_05_03_ImagePlanGenerator/Working/State_progress.json
```

说明：

1. `koubo_storyboard_edit.json` 和 `video_generation_plan.json` 可以不存在。
2. 不存在时必须记录 warning，不应直接失败。
3. run 阶段正式逻辑读取 `Working/` 快照。

## 8. `05_03_ImagePlanGenerator` 参数

建议参数：

```text
--workspace <workspace>
--target-type scene|shot|task
--shot-id <shot_id>
--scene-id <scene_id>
--include-existing-prompts
--include-ready-images
--force
--resume
--print-json
```

参数规则：

1. `--target-type scene` 必须提供 `--shot-id` 和 `--scene-id`。
2. `--target-type shot` 必须提供 `--shot-id`。
3. `--target-type task` 表示当前 Session 的全量 StoryBoard，不访问数据库。
4. `--include-existing-prompts` 表示计划中记录已有 Prompt 的任务状态，默认建议开启。
5. `--include-ready-images` 表示计划中记录已有新图的 ready 任务，默认建议开启，便于 UI 展示全量矩阵。
6. `--force` 只清理本工具目录，不删除 `SessionOutput/storyboard/Working/`。
7. `--resume` 只在输入 hash 一致时复用本工具计划结果。

## 9. 图像来源识别规则

### 9.1 新图

新图是可以直接用于后续视频首帧或 StoryBoard 展示的图片。

来源：

```text
dialogue.working_assets.images[0].path
dialogue.bound_image_path
SessionOutput/storyboard/Working/{asset_key}_Image_01.png
```

规则：

1. 已绑定且文件存在的新图，状态为 `ready_existing_image`。
2. 已绑定但文件不存在，状态为 `blocked_bound_image_missing`。
3. 新图存在时，默认不需要 Prompt Step，也不需要 Image Step。
4. 如果界面强制重生，可以由 Executor 参数选择覆盖，但 Plan 仍要记录已有图片状态。

### 9.2 原始参考帧

原始参考帧只能作为图片生成输入，不等于新图。

来源：

```text
dialogue.image_path
dialogue.key_frame_paths[]
SessionOutput/visual/srt_frames/{srt_id}.jpg
```

规则：

1. 有原始参考帧且没有新图，状态为 `planned_prompt_and_image`。
2. Prompt Step 必须 required。
3. Image Step 必须 required。
4. 生成图片时，原始参考帧作为 `TARGET_FRAME`。
5. UI 槽位状态不能因为“只有原始参考帧”就把 Image Step 显示为白色可执行；只有原始参考帧时，Image Prompt 可以白色可执行，Image 必须保持灰色等待。
6. 只有原始参考帧和当前业务 `ImagePrompt.json` 都存在时，Image Step 才能显示白色可执行。
7. 如果同一 `asset_key` 已经存在 Raw Video 或 Final Video，说明该视觉链路已被下游消费，ImagePlan 不应再提示补新图；Image Prompt 和 Image Step 都应按下游已存在显示为灰色或已完成态，而不是白色可执行。

### 9.3 上传图片池

上传图片池本身不自动等同于新图。

来源：

```text
SessionOutput/storyboard/assets/images/
```

规则：

1. 只有用户把上传图片放入 Dialogue 新图槽位后，才视为新图。
2. 如果上传图片只是存在于 `assets/images/`，Plan 不应把它当作该 Dialogue 的完成图片。
3. 如果 StoryBoard JSON 记录了上传图片已被放置到 Dialogue 新图槽位，Plan 应记录 `materialize_first_frame` 或等价图片物化动作。

### 9.4 已有 Prompt

已有 Prompt 来源：

```text
SessionOutput/storyboard/Working/{asset_key}_ImagePrompt.json
```

规则：

1. 有 Prompt 且无最终图片，状态为 `planned_image_from_existing_prompt`。
2. 有 Prompt 且有最终图片，状态为 `ready_existing_image`，并记录 Prompt 存在。
3. Plan 必须在 StoryBoard 主界面保存后的稳定状态上生成。
4. 如果 StoryBoard、source image、reference 或 template 改变，用户应重新生成 Image Plan，随后重新运行 Prompt Step。
5. `stale_prompt` 用于识别“当前 Prompt 与新 Plan 依赖不一致”的旧状态。
6. `stale_prompt` 不自动删除旧 Prompt，由 Executor 或 UI 决定是否重新生成。
7. 界面必须允许用户手动重新运行所有 Prompt，覆盖当前业务 Prompt 文件。

### 9.5 ImagePlan 槽位颜色口径

ImagePlan 展示状态必须以当前 StoryBoard Working 中的业务文件为准，不能只看计划任务是否 required。

优先级：

1. `SessionOutput/storyboard/Working/{asset_key}_Image_01.*` 存在且非空时，Image 绿色。
2. Raw 或 Final 已存在时，ImagePrompt 和 Image 不再白色提示执行；Raw / Final 视为已经消费了该图像链路。
3. 原始参考帧存在但 `ImagePrompt.json` 不存在时，ImagePrompt 白色可执行，Image 灰色等待。
4. 原始参考帧存在且 `ImagePrompt.json` 存在时，ImagePrompt 绿色，Image 白色可执行。
5. 只有 `ImagePrompt.json` 但缺少原始参考帧或可用新图来源时，ImagePrompt 可显示绿色，但 Image 必须灰色 blocked / waiting，不能白色执行。
6. 当前执行 running / failed 只在规范业务文件不存在时影响颜色；已落盘文件优先绿色。

## 10. 图片任务拆分逻辑

`05_03_ImagePlanGenerator` 的拆分逻辑要和 `05_01_VideoPlanGenerator` 保持一致的遍历和局部阻断原则。

### 10.1 遍历顺序

Task 模式：

```text
shots[] -> scenes[] -> dialogue_items[]
```

Shot 模式：

```text
指定 shot -> scenes[] -> dialogue_items[]
```

Scene 模式：

```text
指定 shot + scene -> dialogue_items[]
```

### 10.2 图片任务粒度

默认粒度：

```text
一个需要生成或确认图片的 Dialogue 生成一个 image task
```

图片任务以 `asset_key` 为稳定键。

推荐 image task id：

```text
{shot_id}_{scene_id}_{asset_key}_image
```

示例：

```text
shot_001_scene_001_srt_0001_01_image
```

### 10.3 与视频 Segment 的关系

如果存在 `video_generation_plan.json`，image task 应记录来源 segment：

```json
{
  "source_video_plan_segment_id": "shot_001_scene_001_segment_001",
  "source_video_plan_run_id": "vp_20260607_000001",
  "source_video_plan_hash": "sha256..."
}
```

规则：

1. Image Plan 不重新拆视频 Segment。
2. Image Plan 可以引用 Video Plan 的 segment id，便于 UI 对齐。
3. 如果没有 `video_generation_plan.json`，Image Plan 仍可从同一个已保存 StoryBoard 状态直接拆图像任务。
4. Image Plan 的任务粒度不应被视频切段时长影响；它关注图片是否需要生成。
5. `05_03_ImagePlanGenerator` 必须是 `05_01_VideoPlanGenerator` 图像相关计划逻辑的子集；同一已保存 StoryBoard 输入下，`05_03` 不得规划出 `05_01` 图像逻辑无法解释的图片任务。
6. `05_03` 与 `05_01` 对新图、原始参考帧、上传图落位、已绑定图片、缺失 source image 的判断必须一致。

### 10.4 多图片 Scene

如果一个 Scene 中多个 Dialogue 各自有原始参考帧或新图槽位：

1. 每个 Dialogue 独立生成 image task。
2. 已有新图的任务为 ready。
3. 只有原始参考帧的任务为 planned。
4. 缺少参考帧的任务为 blocked。

### 10.5 零图片 Scene

如果 Scene 中所有 Dialogue 都没有原始参考帧、新图或可用上传图落位：

1. 每个无法生成图片的 Dialogue 应记录为 blocked 或 skipped。
2. blocked code 建议为 `missing_image_source`。
3. 不因为某个 Dialogue blocked 而中断后续 Scene。

### 10.6 局部 blocked 继续向后

规则：

1. 某个 image task blocked，不阻止同 Shot / 同 Task 后续 image task。
2. 后续 image task 如果有自己的 source image，仍可 planned。
3. 计划整体状态可以是 `completed_with_blocked_items`。

## 11. Image Plan 输出结构

业务输出：

```text
SessionOutput/storyboard/image_generation_plan.json
```

工具输出：

```text
S10_05_03_ImagePlanGenerator/Output/image_generation_plan.json
S10_05_03_ImagePlanGenerator/Report/Result.json
```

推荐结构：

```json
{
  "schema_version": "analysis_v1_image_generation_plan_0.1",
  "tool_name": "05_03_ImagePlanGenerator",
  "plan_run_id": "ip_20260607_000001",
  "plan_hash": "sha256...",
  "created_at": "",
  "target": {
    "target_type": "task",
    "shot_id": "",
    "scene_id": ""
  },
  "source": {
    "storyboard_path": "SessionOutput/storyboard/koubo_storyboard_edit.json",
    "fallback_storyboard_path": "SessionOutput/storyboard/srt_storyboard.json",
    "video_generation_plan_path": "SessionOutput/storyboard/video_generation_plan.json",
    "storyboard_hash": "sha256...",
    "video_generation_plan_hash": "sha256..."
  },
  "summary": {
    "total_tasks": 0,
    "planned_prompt_tasks": 0,
    "planned_image_tasks": 0,
    "ready_existing_images": 0,
    "existing_prompts": 0,
    "stale_prompts": 0,
    "blocked_tasks": 0
  },
  "image_tasks": []
}
```

单个 image task 推荐结构：

```json
{
  "image_task_id": "shot_001_scene_001_srt_0001_01_image",
  "asset_key": "srt_0001_01",
  "shot_id": "shot_001",
  "scene_id": "scene_001",
  "dialogue_ids": ["srt_0001_01"],
  "dialogue_text": "给家里备这个化橘红啊",
  "status": "planned_prompt_and_image",
  "source_video_plan_segment_id": "shot_001_scene_001_segment_001",
  "source_image": {
    "source_type": "original_image",
    "source_path": "SessionOutput/visual/srt_frames/srt_0001_01.jpg",
    "role": "TARGET_FRAME",
    "exists": true,
    "sha256": "sha256..."
  },
  "existing_assets": {
    "image_path": "",
    "image_exists": false,
    "prompt_path": "SessionOutput/storyboard/Working/srt_0001_01_ImagePrompt.json",
    "prompt_exists": false,
    "prompt_status": "missing"
  },
  "planned_outputs": {
    "image_prompt_path": "SessionOutput/storyboard/Working/srt_0001_01_ImagePrompt.json",
    "image_path": "SessionOutput/storyboard/Working/srt_0001_01_Image_01.png"
  },
  "steps": {
    "prompt": {
      "required": true,
      "status": "pending",
      "can_edit_after_generate": true,
      "executor_mode": "prompt"
    },
    "image": {
      "required": true,
      "status": "pending",
      "depends_on_prompt": true,
      "executor_mode": "image"
    }
  },
  "references": {
    "host_reference": {
      "required": false,
      "path": "",
      "exists": false
    },
    "product_reference": {
      "required": false,
      "path": "",
      "exists": false
    }
  },
  "blocked_reason": ""
}
```

## 12. Image Task 状态枚举

建议状态：

```text
ready_existing_image
planned_prompt_and_image
planned_prompt_only
planned_image_from_existing_prompt
blocked_missing_source_image
blocked_bound_image_missing
stale_prompt
skipped
```

语义：

1. `ready_existing_image`：已有可用新图，不需要生成 Prompt 或图片。
2. `planned_prompt_and_image`：有 source image，缺 Prompt 或需要刷新 Prompt，且需要生成图片。
3. `planned_prompt_only`：只计划生成 Prompt，不生成图片。
4. `planned_image_from_existing_prompt`：已有可用 Prompt，但图片缺失，可以直接执行 Image Step。
5. `blocked_missing_source_image`：缺少原始参考帧、新图和可用上传图落位。
6. `blocked_bound_image_missing`：JSON 中有绑定路径，但文件不存在。
7. `stale_prompt`：Prompt 存在但依赖 hash 已变化。
8. `skipped`：当前范围内明确不需要图像生成。

## 13. `05_04_ImagePlanExecutor` 输入

必需输入：

```text
SessionContext/Variables.json
SessionOutput/storyboard/image_generation_plan.json
SessionOutput/storyboard/srt_storyboard.json
```

优先输入：

```text
SessionOutput/storyboard/koubo_storyboard_edit.json
```

执行 Image Step 时还必须读取：

```text
SessionOutput/storyboard/Working/{asset_key}_ImagePrompt.json
```

参考模板来源：

```text
OpenCrew/ToolLibrary/Analysis_V1/Reference/05_02/Image_GPT.md
OpenCrew/ToolLibrary/Analysis_V1/Reference/05_02/Image_Gemini.md
OpenCrew/ToolLibrary/Analysis_V1/Reference/05_02/Image_Grok.md
```

prepare 阶段复制到本工具：

```text
S11_05_04_ImagePlanExecutor/Working/InputFrom_0_Variables.json
S11_05_04_ImagePlanExecutor/Working/InputFrom_7_srt_storyboard.json
S11_05_04_ImagePlanExecutor/Working/InputFrom_7_koubo_storyboard_edit.json
S11_05_04_ImagePlanExecutor/Working/InputFrom_10_image_generation_plan.json
S11_05_04_ImagePlanExecutor/Prompt/Ref_05_02_Image_GPT.md
S11_05_04_ImagePlanExecutor/Prompt/Ref_05_02_Image_Gemini.md
S11_05_04_ImagePlanExecutor/Prompt/Ref_05_02_Image_Grok.md
```

run 阶段规则：

1. 计划读取本工具 Working 快照。
2. 模板读取本工具 Prompt 快照。
3. 业务 Prompt 读取或写入 `SessionOutput/storyboard/Working/{asset_key}_ImagePrompt.json`。
4. 图片模型调用前，必须把业务 Prompt 复制到本工具 Prompt 目录形成执行快照。

## 14. `05_04_ImagePlanExecutor` 参数

推荐参数：

```text
--workspace <workspace>
--mode prompt-only|image-only|prompt-and-image
--image-provider <provider>
--image-model <model>
--target-task-id <image_task_id>
--target-asset-key <asset_key>
--only-missing
--allow-stale-prompt
--overwrite-prompt
--overwrite-image
--force
--resume
--print-json
```

等价布尔参数也可以支持：

```text
--execute-prompt true|false
--execute-image true|false
```

模式语义：

1. `prompt-only`：只执行 Prompt Step，不调用图片模型。
2. `image-only`：不重新生成 Prompt，只读取已有业务 Prompt 生成图片。
3. `prompt-and-image`：先生成或刷新 Prompt，再立即使用当前 Prompt 生成图片。

界面推荐按钮：

```text
生成提示词
生成图片
生成提示词并生成图片
```

参数规则：

1. `image-only` 必须要求业务 Prompt 文件存在。
2. `prompt-only` 不要求图片 provider API key 可用。
3. `prompt-and-image` 需要图片 provider/model 配置和真实 API key 可解析。
4. `--only-missing` 表示已有图片不再重新生成。
5. `--overwrite-prompt` 表示允许覆盖已有业务 Prompt，覆盖前必须备份。
6. `--overwrite-image` 表示允许覆盖已有业务图片，覆盖前必须备份。
7. 默认不允许执行 `stale_prompt` 的 Image Step，除非传入 `--allow-stale-prompt`。
8. `prompt-and-image` 表示用户选择完全自动执行：生成 Prompt 后立即生成 Image，中间不要求人工确认。
9. `prompt-only -> 用户界面修改 Prompt -> image-only` 表示人工可控执行。

## 15. Executor 执行流程

### 15.1 Prompt Only

流程：

1. 读取 image plan。
2. 筛选需要 Prompt Step 的 image task。
3. 复制模板到本工具 Prompt。
4. 调用与 `05_02_VideoPlanExecutor` 图片子流程同一个入口的 Prompt package 生成逻辑。
5. 写入本工具 Prompt 快照：

```text
S11_05_04_ImagePlanExecutor/Prompt/PromptVariables_{asset_key}_Image.json
S11_05_04_ImagePlanExecutor/Prompt/PromptRendered_{asset_key}_ImagePrompt.json
```

6. 将业务 Prompt 写入：

```text
SessionOutput/storyboard/Working/{asset_key}_ImagePrompt.json
```

7. 写入 execution state / result。
8. 不调用图片模型。
9. 不写入最终图片。

### 15.2 Image Only

流程：

1. 读取 image plan。
2. 筛选需要 Image Step 的 image task。
3. 校验业务 Prompt 文件存在。
4. 将业务 Prompt 复制到本工具 Prompt 快照：

```text
S11_05_04_ImagePlanExecutor/Prompt/PromptRendered_{asset_key}_ImagePrompt.json
```

5. 校验 source image、host reference、product reference。
6. 写入模型调用 request 审计：

```text
S11_05_04_ImagePlanExecutor/Prompt/ModelCall_{asset_key}_Image_request.json
```

7. 调用唯一图片 provider 模块。
8. 写入 response 审计：

```text
S11_05_04_ImagePlanExecutor/Prompt/ModelCall_{asset_key}_Image_response.json
```

9. 输出图片到：

```text
S11_05_04_ImagePlanExecutor/Output/{asset_key}_Image_01.png
```

10. 发布图片到：

```text
SessionOutput/storyboard/Working/{asset_key}_Image_01.png
```

11. 同步绑定 StoryBoard JSON。

### 15.3 Prompt And Image

流程：

1. 对每个 image task 先执行 Prompt Step。
2. Prompt Step 成功后立即读取当前业务 Prompt。
3. 执行 Image Step。
4. 如果 Prompt Step 失败，该任务 Image Step blocked。
5. 某个任务失败不阻断后续独立任务。

## 16. Executor 输出

业务输出：

```text
SessionOutput/storyboard/Working/{asset_key}_ImagePrompt.json
SessionOutput/storyboard/Working/{asset_key}_Image_01.png
SessionOutput/storyboard/image_plan_execution_state.json
SessionOutput/storyboard/image_plan_execution_result.json
```

工具输出：

```text
S11_05_04_ImagePlanExecutor/Output/image_plan_execution_state.json
S11_05_04_ImagePlanExecutor/Output/image_plan_execution_result.json
S11_05_04_ImagePlanExecutor/Output/{asset_key}_ImagePrompt.json
S11_05_04_ImagePlanExecutor/Output/{asset_key}_Image_01.png
S11_05_04_ImagePlanExecutor/Report/Result.json
```

Prompt 审计：

```text
S11_05_04_ImagePlanExecutor/Prompt/Ref_05_02_Image_GPT.md
S11_05_04_ImagePlanExecutor/Prompt/Ref_05_02_Image_Gemini.md
S11_05_04_ImagePlanExecutor/Prompt/Ref_05_02_Image_Grok.md
S11_05_04_ImagePlanExecutor/Prompt/PromptVariables_{asset_key}_Image.json
S11_05_04_ImagePlanExecutor/Prompt/PromptRendered_{asset_key}_ImagePrompt.json
S11_05_04_ImagePlanExecutor/Prompt/ModelCall_{asset_key}_Image_request.json
S11_05_04_ImagePlanExecutor/Prompt/ModelCall_{asset_key}_Image_response.json
S11_05_04_ImagePlanExecutor/Prompt/Prompt说明.html
```

## 17. StoryBoard JSON 绑定规则

图片生成成功不能只写文件。

成功条件必须同时满足：

1. 最终图片文件存在：

```text
SessionOutput/storyboard/Working/{asset_key}_Image_01.png
```

2. `srt_storyboard.json` 对应 Dialogue 已绑定：

```text
dialogue.working_assets.images[0].path
dialogue.bound_image_path
```

3. 如果 `koubo_storyboard_edit.json` 存在，也同步同一 Dialogue。
4. JSON 路径指向同一个最终图片文件。
5. 绑定失败时，该 image task 不能显示为绿色完成。

## 18. 覆盖与 History 规则

覆盖以下文件前必须备份：

```text
SessionOutput/storyboard/Working/{asset_key}_ImagePrompt.json
SessionOutput/storyboard/Working/{asset_key}_Image_01.png
SessionOutput/storyboard/srt_storyboard.json
SessionOutput/storyboard/koubo_storyboard_edit.json
```

备份落点：

```text
SessionOutput/storyboard/assets/history/batch_*_05_04_image_plan_executor_backup/
```

规则：

1. 单次执行创建一个 backup batch。
2. 不要每个 image task 都制造一个独立 backup batch。
3. 如果备份失败，覆盖动作必须 blocked 或 failed。
4. `--force` 只清理 `S11_05_04_ImagePlanExecutor/`，不删除 StoryBoard Working 中的业务文件。

## 19. UI 控制逻辑

### 19.1 Image Plan 面板

界面应能展示每个 image task 的两步状态：

```text
Prompt
Image
```

每个步骤状态建议：

```text
pending
running
completed_working
blocked
failed
skipped
stale
```

### 19.2 用户操作

界面推荐支持：

1. 生成当前范围所有 Prompt。
2. 生成当前选中图片 Prompt。
3. 编辑某一张图片 Prompt。
4. 保存 Prompt 到同一个业务文件。
5. 根据当前 Prompt 生成图片。
6. 当前范围 Prompt + Image 连续执行。
7. 只重新生成缺失图片。
8. 显示已有图片、已有 Prompt、stale Prompt 和 blocked reason。
9. 手动重新运行所有 Prompt，并覆盖当前业务 Prompt 文件。
10. 区分当前结果来源于 `05_02_VideoPlanExecutor` 还是 `05_04_ImagePlanExecutor`。

### 19.3 执行互斥规则

`05_02_VideoPlanExecutor` 和 `05_04_ImagePlanExecutor` 都可能写入同一批图片业务文件，因此二者必须互斥运行。

规则：

1. 当 `05_02_VideoPlanExecutor` 正在运行时，界面不得启动 `05_04_ImagePlanExecutor`。
2. 当 `05_04_ImagePlanExecutor` 正在运行时，界面不得启动 `05_02_VideoPlanExecutor`。
3. 二者都允许覆盖同一业务文件，但不能并发覆盖。
4. 覆盖前仍必须执行 History 备份。
5. UI 需要区分每次图片结果来自 `05_02` 还是 `05_04`，避免用户误判当前状态来源。

### 19.4 绿色完成含义

Prompt 绿色：

```text
SessionOutput/storyboard/Working/{asset_key}_ImagePrompt.json 存在，且可解析。
```

Image 绿色：

```text
最终图片文件存在，并且 srt_storyboard.json / koubo_storyboard_edit.json 已绑定同一图片路径。
```

不得仅因为本工具 Output 中有图片，就把界面 Image Step 标记为完成。

### 19.5 ImagePlan 状态管理踩坑记录

以下问题已经在 Image Generation Plan 弹窗实现和联调中出现过，后续实现、重构和回归测试必须覆盖：

1. ImagePlan 的运行状态只能绑定 `SessionOutput/storyboard/image_plan_execution_state.json`。不能恢复或新增 `ui_execution_state` 这类前端本地伪状态源，否则会形成双状态源。
2. `artifact_status` 只能作为后端实时扫描 Working 文件得到的即时快照，用于判断 Prompt/Image 文件是否已存在；它不是执行状态文件，不能替代 `image_plan_execution_state.json`。
3. 顶部 `Prompt`、`Image`、`Prompt + Image` 按钮只负责触发命令和执行中 disabled。黄色运行态和绿色完成态必须显示在下方具体 image task 的 `Prompt` / `Image` 步骤卡片上。
4. 点击执行时，后端必须立即写入具体任务绑定：`current_task_id`、`current_asset_key`、`current_step`、`current_step_status`，并同步写入 `tasks[asset_key].steps[step].status=running_generate`。只写整体 `status=queued/running` 会导致 UI 找不到具体 S1/S2 步骤。
5. `05_04_ImagePlanExecutor` 启动时不得把后端 API 已写入的 `current_task_id/current_asset_key/current_step/current_step_status` 清空；否则黄色运行态会被执行器启动过程吃掉。
6. `Prompt + Image` 必须在同一次执行中按步骤推进：Prompt `running_generate` -> Prompt `completed_working` -> Image `running_generate` -> Image `completed_working`。不能在初始排队时因为 Image 依赖 Prompt 尚未生成就把 Image step 过滤掉。
7. Image 生成可能耗时较长，不能因为顶部按钮从 disabled 变回 enabled 或整体 job 状态结束，就推断 Image step 已完成。Image step 必须等 `tasks[asset_key].steps.image.status=completed_working`。
8. 前端不能用 `mode=prompt-and-image` 或整体 `execution_state.status=running` 猜测哪一步正在运行。UI 必须读取具体 step 状态：`tasks[asset_key].steps.prompt/image.status`。
9. 前端读取旧状态前必须校验 `source_plan_hash` 与当前 `image_generation_plan.plan_hash` 一致；不一致时旧 execution state/result 不能染色当前 plan。
10. 如果 `artifact_status.image_in_working=true`，但 execution step 仍是 `running_generate`，运行态优先显示黄色；不能用 Working 快照提前覆盖本轮运行态。
11. 如果图片文件已经生成，但 UI 仍停在黄色，优先检查前端是否吃到了最终 `image_plan_execution_state.json`，以及步骤卡片 class 是否随 step status 响应式变化。`TaskBadge` 不能把 `is-running` 在创建时算死。
12. 删除旧图片后执行 `image-only` 或 `prompt-and-image`，仍必须先把对应 Image step 写成 `running_generate`；生成并发布成功后再写 `completed_working`。
13. 多 Shot ImagePlan 弹窗布局不能把 body 改成横向 flex，否则 Shot_001 / Shot_002 / Shot_003 会横排。多个 Shot 必须按文档流纵向排列，每个 Shot 内部再做紧凑宽度和左右留白控制。

## 20. 与 `05_02_VideoPlanExecutor` 的关系

不改名 `05_02_VideoPlanExecutor`。

新增图像链路后，`05_01 + 05_02` 和 `05_03 + 05_04` 的关系如下：

1. `05_01 + 05_02` 是视频完整执行组。
2. `05_03 + 05_04` 是图像可控子流程执行组。
3. 两个执行组是 OR 关系，但可以先后运行。
4. 如果先执行 `05_03 + 05_04` 并改变图片、Prompt 或 StoryBoard Working 绑定状态，后续完整运行 `05_01 + 05_02` 是允许且推荐的状态一致性收敛方式。
5. `05_01` 和 `05_03` 必须读取同一个界面保存后的稳定 StoryBoard 状态。
6. `05_03` 必须是 `05_01` 图像相关计划逻辑的子集。
7. `05_02` 和 `05_04` 都可以覆盖图片 Prompt 和图片业务文件，但不能同时运行。
8. `05_02` 不应另起第二套图片 Prompt 模板和图片模型 Python。
9. `05_02` 与 `05_04` 共享唯一图片模块。
10. `05_04` 的 Prompt Step 本质上是把 `05_02` 中“生成 Image Prompt”这一子步骤外置出来，供界面单独触发。
11. `05_04` 的 Image Step 本质上是把 `05_02` 中“根据 Image Prompt 生成图片”这一子步骤外置出来，供界面在 Prompt 修改后单独触发。
12. 因此，`05_02` 和 `05_04` 在图片 Prompt 生成、模板选择、字段提取、参考图角色、cutaway 规则和 provider 调用结构上不得产生差异。

## 21. 敏感信息规则

所有输出不得包含：

```text
API key
Authorization header
Bearer token
x-api-key
cookie
数据库连接串
带签名下载 URL 中的敏感 token
```

允许记录：

```text
provider
model
api_key_ref
has_api_key
prompt_path
reference image relative path
output relative path
endpoint 的脱敏版本
```

真实 API key 只允许在运行时内存中使用。

## 22. 测试与验收

### 22.1 ImagePlanGenerator 测试

必须覆盖：

1. `test_image_plan_task_scope`
2. `test_image_plan_scene_scope`
3. `test_image_plan_shot_scope`
4. `test_existing_bound_image_ready`
5. `test_original_frame_requires_prompt_and_image`
6. `test_existing_prompt_requires_image_only`
7. `test_missing_source_image_blocked`
8. `test_koubo_storyboard_edit_priority`
9. `test_partial_blocked_continue`
10. `test_no_prompt_files_created_by_generator`
11. `test_image_plan_subset_of_05_01_image_logic`
12. `test_05_01_and_05_03_storyboard_hash_match`

关键验收：

1. `05_03` 不创建业务 ImagePrompt。
2. `05_03` 不调用图片模块。
3. `05_03` 输出计划能驱动 UI 展示 Prompt / Image 两步。

### 22.2 ImagePlanExecutor 测试

必须覆盖：

1. `test_prompt_only_writes_working_prompt`
2. `test_image_only_requires_existing_prompt`
3. `test_prompt_and_image_runs_in_order`
4. `test_user_edited_prompt_is_used_for_image`
5. `test_prompt_overwrite_backup`
6. `test_image_overwrite_backup`
7. `test_storyboard_and_edit_json_binding`
8. `test_stale_prompt_blocked_without_allow_flag`
9. `test_unique_image_module_reuse`
10. `test_sensitive_output_scan`
11. `test_prompt_render_matches_05_02_image_subflow`
12. `test_05_02_and_05_04_mutual_exclusion`
13. `test_prompt_and_image_auto_mode`
14. `test_manual_prompt_rerun_overwrites_current_prompt`

关键验收：

1. `prompt-only` 不调用图片模型。
2. `image-only` 不重新生成 Prompt。
3. 用户修改后的同一个 Prompt 文件会被用于图片生成。
4. 图片成功后文件和 JSON 绑定必须一致。
5. 输出中不能出现 raw API key。
6. 同一输入下，`05_04` 生成的图片 Prompt package 必须与 `05_02` 图片子流程生成结果一致。

## 23. 最终确认点

本需求确认以下设计：

1. `05_03_ImagePlanGenerator` 只生成图片任务计划，不生成 Prompt。
2. `05_04_ImagePlanExecutor` 同时负责 Prompt Step 和 Image Step。
3. Prompt Step 和 Image Step 可分开执行，也可连续执行。
4. 系统 Prompt 和用户修改 Prompt 使用同一个业务文件。
5. 图片 Prompt 生成、Prompt 模板和图片 provider Python 与现有 `05_02_VideoPlanExecutor` 图片子流程严格一致，并保持唯一来源。
6. 不修改现有 `05_01_VideoPlanGenerator` 和 `05_02_VideoPlanExecutor` 的名称。
7. Plan Generator 的拆任务逻辑与既有 Video Plan 的遍历、状态和局部 blocked 原则保持一致。
8. `05_01 + 05_02` 与 `05_03 + 05_04` 是 OR 关系；用户可以先跑图像可控子流程，再完整跑视频执行组保障状态一致。
9. `05_01` 与 `05_03` 必须读取同一个界面保存后的稳定 StoryBoard 状态，且 `05_03` 是 `05_01` 图像相关逻辑的子集。
10. Plan 生成发生在 StoryBoard 主界面保存完成之后；任何状态改动后可以重新生成 Plan 并重新运行 Prompt。
11. `prompt-and-image` 是可接受的全自动模式；人工确认流程使用 `prompt-only -> 手动修改 -> image-only`。
12. `05_02` 和 `05_04` 都可以覆盖图片业务文件，但二者不能并发运行，界面必须区分当前结果来源。
