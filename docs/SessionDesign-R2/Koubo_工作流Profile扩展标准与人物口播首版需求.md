# Koubo 工作流 Profile 扩展标准与人物口播首版需求

> 规范更新（2026-07-12）：本文早期版本中“保存时同步写入数据库、`task_meta.json` 和 `Variables.json`”的要求已经废止。工作流参数存储、独立目录、独立 00 与 Variables 取参边界，以同目录的《OpenCrew_独立工作流参数存储与00扩展规范.md》为准：保存阶段以数据库为唯一编辑态主状态；各工作流运行自己的 00 后生成 Variables；后续任务只读取 Variables 和明确的上游产物。

版本：v0.4

状态：需求草案。本文用于指导后续在「任务列表（口播）」顶部继续增加类似「人物口播」的专用流程按钮，并把这些按钮沉淀为可复用的 workflow profile，而不是为每个按钮复制一套脚本生成、StoryBoard 和一键成片逻辑。

v0.4 更新：补充“选中 Task 后点击专用流程按钮”的保存语义和踩坑记录。以后同类 workflow profile 必须支持编辑当前选中 Task、保存到同一 Task，并把运行所需参数同步写入数据库、`task_meta.json` 和 `Variables.json`。

v0.4.1 更新：明确区分“任务级视频分析参考视频”和“Wan R2V 自动带入的模块参考视频”。人物口播任务不需要用户上传 task-level reference video；Wan R2V 在 05_02 阶段自动带入固定 reference video，并结合首帧 / 尾帧执行。

v0.4.2 更新：补充 `TalkingHead_V1/04_01_SRTRewrite.py` 专属改写逻辑。人物口播不能直接继承 Analysis V1 的 `rewrite_final_prompt` 强制阻断规则；应按“已有脚本/SRT直接使用、无脚本则复杂提示词生成、无复杂提示词则简单提示词生成、三者都无才阻断”的优先级执行。

v0.4.3 更新：补充运行弹窗交互契约。`StoryBoard生成`、`重新运行`、`一键成片` 等入口如果会弹出运行面板，则入口点击只允许打开面板、加载将要执行的任务清单并展示当前状态，不得自动启动后台运行；真正启动只能来自面板内的主运行按钮或明确的二次确认。

v0.4.4 更新：补充 ToolLibrary 专属工具 CLI 兼容约束。所有 profile 专属工具必须兼容执行器通用参数，至少包含 `--workspace`、`--force`、`--resume`、`--print-json`；即使某个参数当前不使用，也必须接受并安全忽略，避免续跑模式下被 argparse 阻断。

v0.4.5 更新：补充运行弹窗信息密度规则。运行步骤主列表只展示步骤名、状态和耗时；完成态、等待态和运行态不得显示说明小字。只有失败、阻断、取消或失联状态才允许在步骤行内显示错误原因。

v0.4.6 更新：补充运行步骤顺序标签规则。所有运行面板的步骤行必须在最左侧展示连续顺序标签，从 `1` 开始，便于用户确认将要执行和已经执行的任务顺序。

v0.4.7 更新：修正人物口播 StoryBoard 链路边界。人物口播不得执行 `Analysis_V1/04_03_StoryBoardQuick.py` 后再试图修正结构；`TalkingHead_V1` 专属 StoryBoard 工具必须直接替代 `04_03`，保留“用最终提示词生成故事版结构”的能力，并强制输出 `1 Shot + 1 Scene + 稳定 Dialogue/Segment`。同时补充 HeyGen 克隆声音生成/测时、首帧按 Segment 复用重置、StoryBoard 展示产物同步写入等踩坑记录。

v0.4.8 更新：修正人物口播 Segment / Dialogue 定义。`单个视频长度` 是一个 Dialogue/Segment 的目标上限，不是给每句 SRT 固定时长；`02_StoryBoardStructure` 必须先用选定 HeyGen 克隆声音和 Tempo 真实生成校准音频，计算每个发音单位秒数，再把连续 SRT 合并成不超过单个视频长度的 Dialogue/Segment。`03_StoryBoardConfig` 再为合并后的每个 Dialogue 生成最终音频，并以最终音频真实时长回写 StoryBoard 和字幕 timing 快照。

v0.4.9 更新：明确 HeyGen 调用参数来源。`00_PrepareSessionVariables` 必须把所选 HeyGen voice、Tempo、voice-clone provider/model/api_key_ref 快照写入 `SessionContext/Variables.json`；`02/03` 只能从 Variables 读取任务级声音选择和运行配置。API Key 本体不得写入 Session 文件，只保存 `api_key_ref`，运行时从本地密钥仓库解析。

v0.4.10 更新：补充 Session Variables 刷新入口的 workflow 分发规则。任何 UI 入口只要声称“运行 00 / 刷新 Session Variables”，都不能默认执行 `Analysis_V1/00`；必须先识别当前 Task 来源，动作模拟运行 `DanceMimic_V1/00`，人物口播运行 `TalkingHead_V1/00`，普通脚本/视频分析才运行 `Analysis_V1/00`。

v0.4.11 更新：补充一键成片逐句状态的音频绿灯规则。人物口播不能只用 `SegmentAudio_Final` 判断音频完成；03 已生成每个 Dialogue 的 HeyGen 克隆声音 `Audio_Final`，且 VideoPlan 标记 `sync_mode=lipsync` / `dialogue_marked_talking_head` 时，状态面板应显示 `音频已匹配`。`SegmentAudio_Final` 是 05_02 消费/合成阶段产物，不等同于前序声音是否已存在。

v0.4.12 更新：补充 StoryBoard TTS 面板的口播声音回显与保存规则。人物口播创建页选择的 HeyGen voice / Tempo 必须透传到 StoryBoard `storyboard_tts_selection` 和 Timing 面板；如果旧 StoryBoard edit 中存在 Qwen/Cherry 等历史 selection，人物口播配置优先，不能覆盖或隐藏当前 profile 的 HeyGen 克隆声音。推荐声音后必须显示与当前推荐音色匹配的 Tempo 参数；只修改 Tempo 并保存时，必须保留当前 voice/provider/model/candidate，不得保存成半截 selection。

v0.4.13 更新：补充 StoryBoard TTS 生成与缓存回写规则。点击 `生成TTS` 后不能只产出 wav 或只更新前端状态；后端必须写入 `tts_manifests/*_Audio_Final.json`，同步 `working_assets.audio.path`，并把 Dialogue/Scene/Shot duration 回写为真实音频时长。若读取已有 TTS manifest 作为缓存，也必须在后端加载 StoryBoard 时按 manifest duration 覆盖旧估算值，避免 UI 显示 40 秒但 `srt_storyboard.json` 仍保留旧 45 秒。

v0.3 更新：补充任务条线资产化标准，把“模型选择、人物/空镜类型、Segment 分割方式、尾帧使用方式、固定 StoryBoard 工作流、一键成片客户交付”抽象为通用 workflow profile 能力。

v0.2 更新：补充人物形象照片上传 Tab、HeyGen 克隆声音与 Tempo、ToolLibrary 新工具包、人物口播 00 重写、按声音语速计算 SRT 时长、首帧复用 N 个 Segment、默认 Wan R2V 等差异。

## 1. 背景

当前「任务列表（口播）」已经有三个创建入口：

```text
视频分析
动作模拟
脚本生成
```

本次希望在 `脚本生成` 右侧增加一个新按钮：

```text
人物口播
```

「人物口播」的核心不是新建一套任务系统，而是复用「脚本生成」的创建体验、Task / Session / workspace、Prompt Builder、SRT Rewrite、StoryBoard 生成和后续一键成片链路。它的差异应沉淀为一个可配置的 workflow profile：

1. 默认模型不同。
2. 默认 StoryBoard 生成方式不同。
3. 每个 SRT / Segment 匹配资源的策略不同。
4. 一键成片时的 VideoPlan / VideoOnly / Composer 策略不同。
5. 页面上可能出现独立的 `StoryBoard生成` 和 `一键成片` 按钮。
6. 创建弹窗需要新增人物形象 Tab，上传照片作为人物首帧。
7. 创建弹窗需要能选择 HeyGen 克隆声音和 Tempo。
8. StoryBoard 生成后，默认声音、脚本、第一条 SRT 的新图都要从人物口播创建配置初始化。
9. 一个上传首帧可复用多个 Segment，超出复用窗口后重新把人物形象图放入对应 Segment 的 `Image_New`。
10. 视频模型默认使用 Wan R2V。

更长期的目标是：把每个单独任务条线沉淀为一个可复用的“故事版工作流资产”。每条线的主要差异通常不是 Task / Session / StoryBoard / Composer 这些底座，而是：

1. 不同人物类型或画面类型使用什么模型，例如口播、空镜、产品、动作模拟。
2. Segment 如何分割，例如一句话一段、固定 8 秒一段、按 Shot 分段、按 Scene 分段。
3. 首帧和尾帧如何使用，例如固定首帧、上一段尾帧、每 N 段重置首帧、首尾帧共同约束。
4. 资源缺失时如何处理，例如 blocked、warning、fallback 到某类素材、禁止空镜 fallback。
5. 固定 StoryBoard 工作流如何从内部能力沉淀成可交付给客户的全自动一键成片功能。

### 1.1 Reference Video 术语边界

人物口播流程中有两个容易混淆的 reference video 概念，必须严格区分：

| 名称 | 字段 / 位置 | 谁提供 | 用途 | 人物口播是否需要用户提供 |
| --- | --- | --- | --- | --- |
| Task-level analysis reference video | `openclip_tasks.reference_video_path` / 创建视频分析任务时上传的视频 | 用户上传 | 给普通 Analysis V1 的 `01_VideoProbeMetadata`、`02_01_AudioASR`、`02_02_VideoSRTFrame` 使用，用于从一条原始视频中分析字幕、画面和素材 | 不需要 |
| Wan R2V module reference video | `ToolLibrary/Analysis_V1/Reference/05_02/Video_Wan_R2V.mp4`，05_02 运行时复制到 Working | 系统内置，执行器自动带入 | 给 Wan R2V 提供表情节奏、亲和眼神、自然笑意和松弛口播状态参考；人物身份和画面身份仍由首帧图像提供 | 自动带入，不需要用户上传 |

因此：

1. 人物口播的 StoryBoard 生成阶段不得要求 `reference_video_path`。
2. 人物口播运行 `TalkingHead_V1/00 + TalkingHead_V1/04_01 + TalkingHead_V1/01/02/03` 时，不得触发普通视频分析的 reference video 校验，也不得执行普通 `Analysis_V1/04_03`。
3. Wan R2V 阶段仍会使用 reference video，但这是 05_02 执行器自动带入的模块参考视频，不是任务入口的视频分析参考视频。
4. VideoPlan/Executor 必须把人物首帧、上一段尾帧和 Wan R2V 模块参考视频组合好；用户只需要上传人物形象图、选择声音和设置节奏参数。

## 2. 当前可复用基线

### 2.1 前端入口

当前任务列表入口在：

```text
OpenCrew/frontend/src/modules/koubo/KouboTaskList/KouboTaskCreateMenu.jsx
OpenCrew/frontend/src/modules/koubo/KouboTaskList/KouboTaskListPage.jsx
```

`脚本生成` 使用：

```text
OpenCrew/frontend/src/modules/koubo/KouboTaskList/KouboTaskCreateFromScriptModal.jsx
```

该弹窗已经包含：

1. 脚本输入。
2. 行业、人设、目标受众、视频公式、产品信息、约束条件。
3. SRT Rewrite 与 StoryBoard 两组 prompt。
4. StoryBoard Quick 参数。
5. Prompt Model 选择。
6. 保存任务、生成复杂提示词、进入任务。

「人物口播」首版应复用这个弹窗和数据结构，避免复制出 `KouboTaskCreatePersonTalkingModal.jsx` 这类平行实现。允许增加 `profile` prop，让同一个弹窗按 profile 改默认值、文案和策略说明。

### 2.2 后端脚本创建

当前脚本创建接口在：

```text
OpenCrew/backend/opcrew_backend/koubo/task_list_router.py
POST /api/koubo-tasks/create-from-script
PUT  /api/koubo-tasks/{task_id}/script
```

脚本任务会写入：

```text
SessionOutput/subtitle/source_script.txt
SessionOutput/subtitle/final_srt_frame_items.json
SessionOutput/task_list/task_meta.json
```

其中 `task_meta.json` 已有：

```json
{
  "workflow_id": "script",
  "workflow_mode": "script",
  "create_mode": "script",
  "input_mode": "script_only"
}
```

后续新增专用流程时，应优先在同一接口增加 profile 字段，而不是新增一套完全独立的脚本创建接口。

### 2.3 StoryBoard 运行

当前 Analysis V1 到 StoryBoard 的运行入口在：

```text
POST /api/openclip/tasks/{task_id}/analysis-v1/run-to-storyboard
```

普通脚本任务无参考视频时可以运行不依赖视频的步骤，典型链路是：

```text
00_PrepareSessionVariables
04_01_SRTRewrite
04_03_StoryBoardQuick
```

注意：这是普通脚本生成 / Analysis V1 的基线链路，不是人物口播链路。人物口播不得执行 `04_03_StoryBoardQuick`，必须由 `TalkingHead_V1/01_StoryBoardGenerate.py` 替代其 StoryBoard 生成能力。

如果选择模型分组，也可以用：

```text
04_02_StoryBoard
```

### 2.4 一键成片

一键成片已有通用原则文档：

```text
OpenCrew/docs/SessionDesign-R2/Koubo_OneClickMovie_通用面板与口播首版需求.md
```

关键原则必须继续沿用：

1. 入口按钮只打开面板。
2. 面板内点击主按钮才启动后台运行。
3. 每个业务场景有独立 target。
4. 状态写入独立 state JSON。
5. 支持失败、阻断、服务重启失联、局部续跑。
6. 不允许把动作模拟的一键成片策略直接套到口播或人物口播。

### 2.5 人物形象与 HeyGen 声音现有能力

当前可复用能力：

```text
OpenCrew/backend/opcrew_backend/koubo/koubo_storyboard/asset_digital_human_routes.py
OpenCrew/backend/opcrew_backend/koubo/koubo_storyboard/asset_digital_human_services.py
OpenCrew/frontend/src/modules/koubo/KouboStoryBoard/kouboStoryboardApi.js
OpenCrew/frontend/src/modules/koubo/KouboStoryBoard/components/KouboTimingMenu.jsx
```

已有接口包括：

```text
GET  /api/koubo-storyboard/tasks/{task_id}/asset-library/digital-human/voices
POST /api/koubo-storyboard/tasks/{task_id}/asset-library/digital-human/voices/clone
GET  /api/koubo-storyboard/tasks/{task_id}/asset-library/digital-human/avatars
POST /api/koubo-storyboard/tasks/{task_id}/asset-library/digital-human/avatars/photo
```

已有 StoryBoard Timing 菜单支持：

1. Voice 选择。
2. TTS Tempo 输入。
3. 保存到 Task。
4. 根据候选声音推荐 Tempo。

人物口播创建弹窗不应重新造一套 HeyGen 声音系统，应复用这些后端服务和数据结构，只是把入口前置到创建任务阶段。

## 3. Profile 标准定义

以后每新增一个按钮，都必须先定义一个 profile。Profile 是“同一套基础流程的配置变体”，不是新的任务架构。

### 3.1 Profile 必填字段

建议最小结构：

```json
{
  "profile_id": "person_talking_head_v1",
  "button_label": "人物口播",
  "create_mode": "person_talking_head",
  "input_mode": "script_only",
  "workflow_id": "person_talking_head_v1",
  "workflow_mode": "script",
  "display_source_label": "人物口播",
  "default_prompt_model": {
    "preset": "max",
    "providerID": "",
    "modelID": ""
  },
  "default_run_model": {
    "preset": "max",
    "providerID": "",
    "modelID": ""
  },
  "storyboard": {
    "default_mode": "quick",
    "show_generate_button": true
  },
  "one_click_movie": {
    "enabled": true,
    "target": "person_talking_head_v1_one_click_movie"
  },
  "tool_library": {
    "package_name": "TalkingHead_V1",
    "custom_step_ids": ["00", "04_01", "01", "02", "03"],
    "reuse_analysis_v1_step_ids": ["05_01", "05_02", "06_01"]
  },
  "input_tabs": [
    "script",
    "portrait",
    "voice_timing"
  ],
  "portrait": {
    "required": true,
    "upload_root": "talking_head_v1/portrait_images",
    "apply_to_first_segment": true
  },
  "voice_timing": {
    "provider": "heygen",
    "voice_source": "clone_or_existing",
    "tempo": 1.0,
    "use_tempo_to_estimate_srt_duration": true
  },
  "segment_planning": {
    "shot_policy": "single_shot",
    "scene_policy": "single_scene",
    "segment_policy": "merge_srt_to_single_video_length",
    "srt_target_seconds": 8.0,
    "portrait_segments_per_image": 2,
    "allow_sentence_split": false
  },
  "video_model": {
    "provider": "wan",
    "model": "wan2.7-r2v"
  },
  "asset_type_model_matrix": {
    "talking_head": {
      "provider": "wan",
      "model": "wan2.7-r2v"
    },
    "cutaway": {
      "enabled": false,
      "provider": "",
      "model": ""
    }
  },
  "tail_frame_policy": {
    "mode": "previous_segment_tail_frame",
    "reset_every_segments": 2,
    "reset_source": "uploaded_portrait_image",
    "fallback": "uploaded_portrait_image"
  },
  "storyboard_asset": {
    "asset_id": "talking_head_storyboard_v1",
    "is_customer_deliverable": true,
    "automation_level": "one_click_movie",
    "locked_steps": ["00", "04_01", "01", "02", "03", "05_01", "05_02", "06_01"]
  },
  "resource_strategy": {
    "kind": "talking_head_only",
    "allow_cutaway": false
  }
}
```

字段规则：

| 字段 | 规则 |
| --- | --- |
| `profile_id` | 代码、状态文件、审计事件的稳定 ID。不可用中文。 |
| `button_label` | 页面按钮文案。 |
| `create_mode` | 任务列表「来源」展示和筛选用。不得继续全部写成 `script`。 |
| `input_mode` | 输入形态。人物口播首版仍是 `script_only`。 |
| `workflow_id` | 写入 `task_meta.json`、StoryBoard schema、state JSON。 |
| `workflow_mode` | 底层兼容模式。人物口播首版可继续复用 `script`。 |
| `display_source_label` | 任务列表展示文案。 |
| `default_prompt_model` | Prompt Builder 默认模型策略。 |
| `default_run_model` | StoryBoard 运行默认模型策略。 |
| `storyboard.default_mode` | `quick` 或 `model`。 |
| `one_click_movie.target` | 独立一键成片 target，不能复用 analysis 或 dance mimic target。 |
| `tool_library.package_name` | ToolLibrary 新工具包英文名。人物口播建议 `TalkingHead_V1`。 |
| `input_tabs` | 创建弹窗需要出现的业务 Tab。人物口播新增 `portrait` 和 `voice_timing`。 |
| `portrait` | 人物形象图配置。必须记录上传路径、是否作为首段首帧。 |
| `voice_timing` | 默认声音和语速配置。人物口播首版使用 HeyGen voice clone / existing voice。 |
| `segment_planning` | 由脚本、SRT 目标秒数和可选声音语速派生 Shot / Scene / Segment 的规则。 |
| `video_model` | 该 profile 的默认视频模型。人物口播首版指定 `wan/wan2.7-r2v`。 |
| `asset_type_model_matrix` | 不同画面 / 人物类型的模型矩阵。口播、空镜、产品、动作模拟可以各自指定模型。 |
| `tail_frame_policy` | 尾帧使用方式。决定是否用上一段尾帧、每 N 段重置首帧、失败时如何回退。 |
| `storyboard_asset` | 固定故事版工作流资产定义。用于把内部流程沉淀成客户可使用的一键成片功能。 |
| `resource_strategy.kind` | SRT / Segment 资源匹配策略。 |

### 3.2 Profile 保存位置

首版可以先在前端和后端各放一个小型 profile registry：

```text
OpenCrew/frontend/src/modules/koubo/KouboTaskList/kouboWorkflowProfiles.js
OpenCrew/backend/opcrew_backend/koubo/workflow_profiles.py
```

后续 profile 增多后，再抽到统一 JSON：

```text
OpenCrew/config/koubo_workflow_profiles.json
```

不建议把 profile 默认值散落在按钮、弹窗、后端路由和工具脚本里。否则后续每新增一个按钮都会出现“前端文案、后端 create_mode、StoryBoard workflow_id、一键成片 target 不一致”的问题。

### 3.3 任务条线的标准差异面

以后新增“人物口播”“空镜成片”“产品口播”“动作模拟成片”等专用按钮时，应优先回答四个问题：

| 差异面 | 要定义什么 | 示例 |
| --- | --- | --- |
| 模型矩阵 | 每类画面 / 人物类型默认用什么生成模型 | 口播用 Wan R2V，空镜用通用 I2V / T2V，动作模拟用动作参考链路。 |
| Segment 分割 | StoryBoard 如何从脚本切成 Segment | 一句口播一个 Segment；空镜可按 6-8 秒重组；动作模拟可按动作段落切分。 |
| 首尾帧策略 | 每段视频如何获得首帧，是否使用上一段尾帧 | 人物口播第 1、3、5... 段重置人物首帧，其余段使用上一段尾帧。 |
| 资源匹配策略 | 每个 Segment 如何找图片、音频、视频、参考素材 | 人物口播禁止空镜；空镜流程优先素材库；产品流程优先产品资产。 |
| 失败与回退 | 缺素材、缺声音、缺人物首帧或模型内置参考资源时如何处理 | 客户交付型一键成片应优先明确 blocked，不静默生成错误内容。 |

这几个差异面应全部写入 profile，并传递到 StoryBoard、VideoPlan、Executor 和一键成片状态面板。不要把这些差异写死在某个按钮或某个工具脚本里。

### 3.4 故事版工作流资产化标准

一个固定 StoryBoard 工作流只有满足以下条件，才算可以沉淀成客户可交付的一键成片资产：

1. 输入可标准化：客户只需要提供脚本、人物图、声音、产品素材等有限参数。
2. 分段规则可预测：同一份输入重复运行时，Segment 边界和 `dialogue_asset_key` 稳定。
3. 模型选择可配置：不同资产类型的模型由 profile 决定，而不是由用户每次手动选择。
4. 首尾帧策略可解释：每段视频为什么用这张首帧、为什么接上一段尾帧，状态面板能追踪。
5. 阻断原因可读：无法全自动成片时，客户看到的是缺什么、怎么补，而不是后台失败。
6. 产物可复用：StoryBoard、VideoPlan、执行状态和最终合成片都能作为下一次任务的模板或审计记录。

因此，标准交付形态不是“给客户一个复杂工具箱”，而是给客户一个具体按钮：

```text
创建任务 -> StoryBoard生成 -> 一键成片 -> 查看阻断/续跑/下载成片
```

内部仍然保留完整的 StoryBoard 编辑、VideoPlan、局部续跑能力；客户默认看到的是被 profile 封装后的稳定流程。

## 4. 标准创建流程

### 4.1 按钮行为

新增按钮必须遵守：

1. 按钮放在 `脚本生成` 右侧。
2. 点击按钮只打开创建弹窗，并传入对应 profile。
3. 不在按钮点击时创建 Task。
4. 不在按钮点击时启动 StoryBoard 或一键成片。
5. 弹窗标题和主按钮可以按 profile 改文案，例如 `创建人物口播任务`。

建议顺序：

```text
视频分析
动作模拟
脚本生成
人物口播
```

### 4.2 创建弹窗复用规则

同一个 `KouboTaskCreateFromScriptModal` 应支持：

```jsx
<KouboTaskCreateFromScriptModal profile={activeProfile} />
```

Profile 只允许影响：

1. 默认行业 / 人设 / 视频公式。
2. 默认 StoryBoard Quick 参数。
3. 默认 Prompt Model / Run Model 选择。
4. 默认 `rewrite_simple_prompt` 和 `storyboard_simple_prompt` 生成模板。
5. 提交 payload 中的 `profile_id`、`create_mode`、`workflow_id`。
6. 弹窗标题、按钮文案、任务创建成功提示。

Profile 不应改变：

1. Task / Session / workspace 创建方式。
2. `source_script.txt` 和 `final_srt_frame_items.json` 的标准落盘。
3. `rewrite_final_prompt` / `storyboard_final_prompt` 的保存字段。
4. 进入 Analysis V1 / StoryBoard 的路由。

### 4.2.1 选中 Task 后的保存语义

这是 workflow profile 创建弹窗的硬约束。

当用户在任务列表中已经选中某个 Task，再点击 `人物口播` 或后续任意专用流程按钮时：

1. 按钮只负责打开同一个创建 / 编辑弹窗，不得清空当前 `selectedTaskId`。
2. 弹窗必须以当前选中 Task 为编辑对象，而不是默认进入新建态。
3. 弹窗初始化必须从 `serialize_task()` 返回的数据回填当前 Task 参数。
4. 点击保存时，如果存在 `selectedTaskId`，必须调用该 profile 的 `PUT / update` 接口更新当前 Task。
5. 只有没有选中 Task 时，保存才允许调用 `POST / create` 接口创建新 Task。
6. 如果允许把普通 `script` Task 转成人物口播 Task，更新接口必须把该 Task 的 `profile_id / create_mode / workflow_id / talking_head` 等参数补齐，而不是新建一条 Task。
7. 保存成功后，任务列表仍应选中同一个 `task_id`，并显示“已更新人物口播任务 #id”一类反馈。

弹窗回填字段至少包括：

| 字段来源 | 必须回填到弹窗 |
| --- | --- |
| task 基础字段 | `source_script`、行业、人设、目标受众、视频公式、产品信息、约束条件 |
| prompt 字段 | 脚本改写提示词、脚本改写最终提示词、故事版创建提示词、故事版最终提示词 |
| profile 字段 | `profile_id`、`create_mode`、`storyboard_quick_config` |
| 人物口播字段 | `portrait_image_path`、`voice_id`、`voice_label`、`tempo`、`srt_target_seconds`、`portrait_segments_per_image` |

人物口播首版的特别要求：

1. 如果用户没有重新上传人物照片，更新时必须保留原 `portrait_image_path`。
2. 如果用户只改声音、语速或首帧覆盖视频个数，不得清掉原脚本、原人物图或已存在 workspace。
3. 人物口播默认值只允许在“新建人物口播 Task”时补齐；编辑已有 Task 时不得用默认值覆盖已保存值。
4. `storyboard_quick_config.target_shot_seconds` 是单个视频长度，同时同步为 `srt_target_seconds`。
5. 更新接口必须同步写入数据库字段、`SessionOutput/task_list/task_meta.json` 和 `SessionContext/Variables.json`，因为后续 `TalkingHead_V1/00` 和 Analysis V1 工具会从这些位置读取参数。

### 4.3 人物形象与声音 Tab

人物口播创建弹窗需要在脚本配置之外新增业务 Tab。

首版推荐拆成：

```text
脚本配置
人物形象
声音与节奏
```

#### 4.3.1 人物形象 Tab

功能：

1. 上传人物照片。
2. 预览照片。
3. 保存为当前人物口播任务的形象锚点。
4. 该照片默认作为第一个 Segment 的首帧 / `Image_New`。
5. 支持后续每隔 N 个 Segment 重新注入一次同一人物形象图。

上传路径建议参考动作模拟，但使用独立根目录：

```text
~/.opencrew/talking_head_v1/portrait_images/
```

不要复用：

```text
~/.opencrew/dance_mimic_v1/target_images/
```

原因：动作模拟目标图和人物口播首帧图虽然都是人物图，但业务语义不同。人物口播图是首帧 / host portrait anchor；动作模拟图是动作迁移目标身份。

Task workspace 中应记录相对路径和来源：

```json
{
  "portrait": {
    "source": "upload",
    "portrait_image_path": "SessionInput/talking_head/portrait_images/<file>.png",
    "portrait_image_original_path": "/Users/.../.opencrew/talking_head_v1/portrait_images/<file>.png",
    "role": "host_portrait_first_frame"
  }
}
```

#### 4.3.2 声音与节奏 Tab

功能：

1. 列出 HeyGen 已有克隆声音。
2. 上传音频并克隆 HeyGen 声音。
3. 可选选择默认 voice。
4. 可选选择 Tempo。
5. 如果已选择 voice + tempo，则可用于估算每条 SRT 的时长；如果创建时未选择声音，后续可在 StoryBoard 页面补选。

推荐字段：

```json
{
  "voice_timing": {
    "provider": "heygen",
    "model": "heygen-voice-clone-v3",
    "voice_id": "",
    "voice_label": "",
    "tempo": 1.0,
    "tempo_source": "manual",
    "seconds_per_char": 0.22,
    "duration_estimation": "voice_tempo"
  }
}
```

节奏计算规则：

1. 如果输入是 SRT 且已有时间轴，默认保留时间轴，但可以提供“按声音语速重算时间轴”开关。
2. 如果输入是普通脚本，优先按所选 HeyGen voice + Tempo 估算每条 SRT 的时长；未选择声音时，按 `srt_target_seconds` 参数生成初始 SRT 时间轴。
3. 估算后不得断句；一句话就是一个 Dialogue，也是一个 Segment。
4. 如果某句估算时长超过目标 SRT 秒数，例如 8 秒，不自动拆句，应标记 warning；首版默认不 blocked。
5. 估算结果写入 `final_srt_frame_items.json` 的 `start/end/duration`，并在 `task_meta.json` 中保留计算参数。

### 4.4 标准任务条线的两个工作页面

这类固定工作流任务，一般都应形成两个稳定页面：

```text
SRT 工作台页面
StoryBoard 工作台页面
```

其中 SRT 工作台页面类似动作模拟的 SRT 页面，负责把“输入脚本 / SRT / 人物图 / 声音 / Tempo / Segment 参数”整理成可执行的结构化输入；StoryBoard 工作台页面负责查看、编辑、绑定和确认 StoryBoard，再进入一键成片。

#### 4.4.1 SRT 工作台页面

SRT 工作台页面的职责：

1. 展示任务基础信息、profile、模型默认值、人物类型 / 画面类型。
2. 展示或编辑原始脚本、改写后的 SRT、每条 SRT 的时间轴。
3. 展示每条 SRT 对应的 Segment 规划结果。
4. 展示人物照片、声音、Tempo、首帧复用参数。
5. 支持重新计算 SRT 时长和 Segment 分组。
6. 支持进入 `StoryBoard生成`。
7. 支持把当前 SRT / Segment 规划保存为任务资产。

页面形态参考动作模拟任务详情页：

```text
#/dance-mimic/tasks/{task_id}
OpenCrew/frontend/src/modules/koubo/DanceMimicV1/DanceMimicV1Module.jsx
```

人物口播建议形成独立路由：

```text
#/talking-head/tasks/{task_id}
```

界面结构：

1. 顶部任务头：显示步骤编号、任务线名称、运行状态、Task / Session 标签。
2. 顶部动作区：`任务列表`、`重新运行`、`一键成片`、`强制重建`、`故事板`、折叠按钮。
3. 参数折叠区：展示人物照片、HeyGen voice、Tempo、首帧复用参数、StoryBoard 是否已生成。
4. 错误 / 阻断提示区：展示缺人物图、缺声音、SRT 为空、生成失败等原因。
5. 逐句拆解面板：标题使用 `逐句人物口播拆解`，副标题显示 `N 句对白`。
6. 逐句列表：每条展示序号、SRT 文本、时间范围、Segment 状态、首帧来源。
7. 运行弹窗：复用通用 `RunProgressDialog` 形态，展示 `运行变量准备 / 口播脚本改写 / 故事版生成 / 故事版分镜生成 / 故事版配置`。
8. 一键成片弹窗：复用动作模拟的一键成片弹窗信息架构，但 target 和策略使用人物口播 profile。

按钮语义：

| 按钮 | 人物口播语义 |
| --- | --- |
| `任务列表` | 回到 `#/koubo-tasks`。 |
| `重新运行` | 打开人物口播 SRT / StoryBoard 运行弹窗，只展示当前运行状态；不得在弹窗打开时自动启动。 |
| `一键成片` | 打开人物口播一键成片面板。若 StoryBoard 未生成，可以先提示将自动生成 StoryBoard。 |
| `强制重建` | 忽略已存在的中间产物，重新跑人物口播 00 / SRT / StoryBoard 准备流程；如果需要弹出运行面板，必须等待用户在面板内再次点击运行。 |
| `故事板` | 打开 `#/koubo-storyboard/tasks/{task_id}`。 |
| 折叠按钮 | 折叠 / 展开参数区或逐句拆解区。 |

人物口播的 SRT 工作台默认规则：

1. SRT 是源句子；Dialogue/Segment 是按单个视频长度合并后的生成单元。
2. 不自动断句，不拆分单条 SRT。
3. 时间轴由 `srt_target_seconds` / 单个视频长度、HeyGen voice + Tempo 校准、以及最终 Dialogue 音频真实时长共同决定。
4. 每个 Segment 展示包含的 `srt_ids`，以及是否为人物照片锚点段。
5. 每个 Segment 展示 first frame 来源：上传人物图或上一段尾帧。

#### 4.4.2 StoryBoard 工作台页面

StoryBoard 工作台页面的职责：

1. 展示 StoryBoard 的 Shot / Scene / Dialogue / Segment 层级。
2. 展示每条 Dialogue 的 `dialogue_asset_key`。
3. 展示 `Image_New`、音频、视频、首尾帧、模型策略和阻断状态。
4. 支持人工微调 StoryBoard。
5. 支持从已确认 StoryBoard 进入 `一键成片`。
6. 支持局部续跑和状态追踪。

人物口播的 StoryBoard 工作台默认规则：

1. 禁止自动匹配空镜。
2. 第一条 SRT 的 `Image_New` 默认绑定上传人物照片。
3. 后续按 `portrait_segments_per_image` 周期重新注入人物照片。
4. 非锚点 Segment 优先使用上一段尾帧。
5. 视频执行模型默认 Wan RTV，复用 Analysis V1 已实现的 Python。

#### 4.4.3 两个页面之间的数据边界

SRT 工作台产出：

```text
source_script.txt
final_srt_frame_items.json
SessionContext/Variables.json
task_meta.json
```

StoryBoard 工作台消费这些产物，并产出：

```text
srt_storyboard.json
koubo_storyboard_edit.json
VideoPlan
one_click_movie_state.json
```

两个页面之间的稳定连接点必须是：

1. `task_id` / `session_id`。
2. `profile_id` / `workflow_id`。
3. SRT item stable id。
4. `dialogue_asset_key`。
5. Segment index 和 Segment asset key。

不得只靠显示文本、当前行号或临时数组 index 连接资源。

### 4.5 后端创建规则

`POST /api/koubo-tasks/create-from-script` 应接受：

```json
{
  "profile_id": "person_talking_head_v1",
  "create_mode": "person_talking_head"
}
```

后端不得盲信前端传入的中文文案。后端应按 `profile_id` 查 registry 后写入：

```text
openclip_tasks.workflow_mode = script
SessionOutput/task_list/task_meta.json
```

`task_meta.json` 建议写入：

```json
{
  "schema_version": "koubo_task_list_meta_0.2",
  "workflow_id": "person_talking_head_v1",
  "workflow_mode": "script",
  "profile_id": "person_talking_head_v1",
  "create_mode": "person_talking_head",
  "input_mode": "script_only",
  "display_source_label": "人物口播",
  "resource_strategy": {
    "kind": "talking_head_only",
    "allow_cutaway": false
  },
  "portrait": {
    "portrait_image_path": "SessionInput/talking_head/portrait_images/<file>.png",
    "role": "host_portrait_first_frame"
  },
  "voice_timing": {
    "provider": "heygen",
    "voice_id": "",
    "tempo": 1.0,
    "duration_estimation": "voice_tempo"
  },
  "segment_planning": {
    "shot_policy": "single_shot",
    "scene_policy": "single_scene",
    "segment_policy": "merge_srt_to_single_video_length",
    "srt_target_seconds": 8.0,
    "portrait_segments_per_image": 2,
    "allow_sentence_split": false
  },
  "video_model": {
    "provider": "wan",
    "model": "wan2.7-r2v"
  }
}
```

任务列表序列化时，优先从 `task_meta.json.create_mode` 和 profile registry 得到展示标签。

## 5. 标准 StoryBoard 生成流程

### 5.1 按钮语义

专用流程页面一般可以有 `StoryBoard生成` 按钮。该按钮语义是：

1. 打开 StoryBoard 生成运行面板，并立即加载本次将执行的步骤清单。
2. 只运行到 `srt_storyboard.json` / `koubo_storyboard_edit.json` 可编辑状态。
3. 不生成视频。
4. 不调用一键成片。
5. 不在面板打开时自动启动运行；只有用户点击面板内主运行按钮，才创建新的 attempt 并启动后台任务。
6. 面板打开后即使没有历史 attempt，也必须展示计划步骤，例如人物口播展示 `00 / 04_01 / 01 / 02 / 03`，不能显示空白或只显示“尚未运行”。
7. 步骤主列表必须保持紧凑，只显示步骤名、状态和耗时；完成态的 passthrough、warning、summary 等说明不得以小字显示在步骤行内。
8. 只有 `failed` / `blocked` / `cancelled` / `stale_running` 这类需要用户处理的异常状态，才允许在步骤行内显示错误原因。
9. 每个步骤行必须展示顺序标签，位置在步骤名称左侧，数字从 `1` 开始连续递增；预览态和运行态都必须一致。

如果为了和现有 Analysis V1 运行弹窗一致，首版可以让按钮打开现有 run-to-storyboard 面板；但 profile 必须决定默认执行模式。

### 5.2 StoryBoard 模式

Profile 必须声明默认模式：

| 模式 | 适用 | 工具 |
| --- | --- | --- |
| `quick` | 有稳定节奏参数、追求快、无需模型重新组织结构 | `04_03_StoryBoardQuick.py` |
| `model` | 需要模型按内容重新组织 Shot / Scene | `04_02_StoryBoard.py` |

人物口播不得直接执行 `Analysis_V1/04_03_StoryBoardQuick.py`。如果需要保留“根据 StoryBoard 最终提示词生成故事版结构”的能力，必须迁移或封装到 `TalkingHead_V1/01_StoryBoardGenerate.py` 内，由 `01` 直接输出人物口播专属 StoryBoard 初稿，并在后续 `02` 中强制规整为 `1 Shot + 1 Scene + 按单个视频长度合并后的 Dialogue/Segment`。普通 `04_03` 的输出不可信，不能作为人物口播最终结构来源。

人物口播首版建议：

```json
{
  "storyboard": {
    "default_mode": "quick",
    "single_video_length_seconds": 8,
    "split_tolerance_seconds": 0,
    "language_boundary_mode": "strict",
    "shot_policy": "single_shot",
    "scene_policy": "single_scene",
    "segment_policy": "merge_srt_to_single_video_length",
    "srt_target_seconds": 8,
    "allow_sentence_split": false
  }
}
```

原因：人物口播没有空镜，画面资源策略更依赖后续人物一致性和口型同步；首版 StoryBoard 应稳定保持 SRT 顺序和自然句边界，但 Dialogue/Segment 是视频生成单元，不等同于单句 SRT。人物口播首版固定采用 `1 Shot + 1 Scene + N Segment`，其中 `N Segment` 由 `02` 根据单个视频长度、HeyGen 克隆声音和 Tempo 计算后合并连续 SRT 得到；不得把单个视频长度误解为每句 SRT 的固定秒数。

### 5.3 人物口播 StoryBoard 初始化规则

生成 StoryBoard 后需要立即完成以下默认绑定：

1. 顶层写入 `workflow_id=person_talking_head_v1`。
2. 顶层写入 `resource_strategy.kind=talking_head_only`。
3. 顶层或 meta 写入 `portrait` 和 `voice_timing` 快照。
4. `SessionContext/Variables.json` 必须写入 `talking_head.voice_timing`、`talking_head.voice_clone_config` 和 `default_voice_clone_config`，其中包含所选 `voice_id / voice_label / tempo`、HeyGen provider/model、`api_key_ref`、`has_api_key` 和配置来源；不得写入 API Key 明文。
4. 每条 Dialogue 保留原始脚本文案或改写后的脚本文案。
5. 每条 Dialogue 都有唯一稳定 `dialogue_asset_key`。
6. 第一条 SRT 的 `working_assets.images[0]` / `Image_New` 绑定上传人物照片。
7. 默认声音写入 StoryBoard 的音频设置，使后续 TTS / SegmentAudio 使用该 HeyGen voice 和 Tempo。
8. `srt_storyboard.json` 和 `koubo_storyboard_edit.json` 都必须同步这些默认绑定。

首帧复用规则：

```text
portrait_segments_per_image = 2
S1 使用上传人物照片作为 Image_New
S2 复用 S1 的尾帧或同一人物照片，不重新注入上传图
S3 再次把上传人物照片放入 Image_New
S4 复用 S3 的尾帧或同一人物照片
S5 再次把上传人物照片放入 Image_New
```

即：如果一个首帧可以覆盖 2 个 Segment，则第 1、3、5、7... 个 Segment 是形象锚点段，必须显式绑定人物照片到新图。其余 Segment 可以使用上一段尾帧，以保持连续。

### 5.4 结构身份

所有 profile 都必须继续遵守 StoryBoard 资源绑定金标准：

1. 每条 Dialogue 必须有稳定 `dialogue_asset_key`。
2. 后续音频、图片、视频、VideoPlan、VideoOnlyPlan、Composer 都以 `dialogue_asset_key` 或 segment `asset_key` 绑定。
3. 不允许运行时用 `srt_id`、序号、显示文本模糊匹配资源。
4. 结构变化后必须更新 StoryBoard 签名，旧 plan 变 stale。

这条规则对人物口播尤其重要，因为同一个人脸 / 形象资源会跨多个 SRT 连续使用，一旦 key 漂移，就会出现串音、串图、串视频。

## 6. 标准一键成片流程

### 6.1 按钮语义

每个 profile 的 `一键成片` 按钮必须遵守：

1. 页面入口只打开一键成片面板。
2. 面板内主按钮才启动运行。
3. 独立 target。
4. 独立状态文件。
5. 支持局部续跑。
6. 逐段状态必须从真实 VideoPlan / VideoOnly / Composer 文件读取。
7. 如果 StoryBoard 未生成，面板可以提示“一键成片会先生成 StoryBoard”，但仍不能在打开面板时自动创建 attempt。
8. 面板打开后必须立即展示将要执行的完整任务清单；人物口播至少包括 `00 / 04_01 / 01 / 02 / 03 / 05_01 / 05_02 / 06_01`。

人物口播建议 target：

```text
person_talking_head_v1_one_click_movie
```

状态文件建议：

```text
SessionReport/person_talking_head_v1/one_click_movie_state.json
SessionReport/person_talking_head_v1/one_click_movie/<run_id>.json
```

### 6.2 标准步骤

人物口播首版建议：

| 顺序 | Step ID | 中文名 | 工具 / 动作 |
| --- | --- | --- | --- |
| 1 | `00` | 运行变量准备 | `TalkingHead_V1/00_PrepareSessionVariables.py` |
| 2 | `04_01` | 口播脚本改写 | `TalkingHead_V1/04_01_SRTRewrite.py` |
| 3 | `01` | 故事版生成 | `TalkingHead_V1/01_StoryBoardGenerate.py`，保留最终提示词生成结构能力 |
| 4 | `02` | 故事版分镜生成 | `TalkingHead_V1/02_StoryBoardStructure.py`，强制 `1 Shot + 1 Scene + 每句一个 Dialogue/Segment` |
| 5 | `03` | 故事版配置 | `TalkingHead_V1/03_StoryBoardConfig.py`，安放首帧并逐句生成声音 |
| 6 | `05_01` | 生成人物口播视频计划 | `05_01_VideoPlanGenerator.py`，profile settings |
| 7 | `05_02` | 逐段生成人物口播视频 | `05_02_VideoPlanExecutor.py` |
| 8 | `06_01` | 合并成片 | `06_01_VideoPlanComposer.py` |

如果用户已手动生成并确认 StoryBoard，一键成片可从 `05_01` 开始，并把 `00/04_01/01/02/03` 标记为 `reused`。

### 6.3 不允许复用动作模拟策略

人物口播不得照搬动作模拟的一键成片策略：

1. 不默认强制 `--no-execute-lipsync`。
2. 不默认强制 `--execute-audio-video-sync`。
3. 不把 Wan R2V 模块参考视频当作每段最终视频。
4. 不默认使用动作参考视频的 `input_references` 模式。
5. 不要求用户为人物口播 Task 上传 task-level analysis reference video。

人物口播默认应尊重当前 VideoPlan 中每段的：

```text
need_lipsync
sync_mode
first_frame.source_type
target_asset_key
```

补充约束：

1. 默认视频模型必须是 `wan/wan2.7-r2v`。
2. `05_01` 需要为每个 Segment 写入 Analysis V1 现有 Wan RTV Python 所需的参数。
3. 不重新设计 Wan RTV 执行器；人物口播只负责在 VideoPlan 中把首帧、尾帧策略、声音、Segment key 和模型选择准备好。
4. 不得在执行阶段静默降级到其他模型，除非用户在 profile 中明确允许 fallback。

## 7. SRT / Segment 资源匹配策略标准

每个 profile 必须明确 `resource_strategy`。这是后续生成专用按钮的核心差异点。

### 7.1 通用策略字段

```json
{
  "resource_strategy": {
    "kind": "talking_head_only",
    "allow_cutaway": false,
    "default_segment_scope": "dialogue_or_short_scene",
    "first_frame_policy": "host_reference_then_previous_tail",
    "image_prompt_policy": "person_consistency",
    "video_prompt_policy": "talking_head_delivery",
    "audio_policy": "tts_or_voice_clone_required",
    "lipsync_policy": "prefer_lipsync",
    "fallback_policy": "block_when_host_reference_missing"
  }
}
```

### 7.2 人物口播首版资源策略

人物口播的业务约束是：

```text
仅生成人物口播，不生成空镜。
```

因此默认策略应为：

| 项 | 人物口播规则 |
| --- | --- |
| 画面类型 | 只允许 talking head / 半身口播 / 产品人物同框，不允许纯产品空镜、环境空镜、转场空镜。 |
| SRT 绑定 | 每个 SRT 或短 Scene 都要绑定到同一个人物身份锚点。 |
| 首帧 | 第一段使用人物形象照片；后续按 `portrait_segments_per_image` 周期重新注入人物照片，其余段可用上一段尾帧。 |
| 图片生成 | 生成或复用人物一致性的 `Image_New`，不从空镜素材库自动匹配。 |
| 视频生成 | 每段生成“人物正在说这句口播”的视频，不生成插画式 B-roll。 |
| 音频 | 默认使用 HeyGen 克隆声音 + Tempo 生成 TTS / SegmentAudio，不能静音成片。 |
| 口型 | 默认优先口型同步；如果所选视频模型原生生成带口播视频，则需在 plan 中明确 `need_lipsync=false` 和原因。 |
| 空镜 fallback | 禁止。缺少人物参考或声音配置时应 blocked，而不是用空镜补。 |

建议 first frame policy：

```text
第 1 个 Segment：uploaded_portrait_image
第 N 个 Segment：当 (index - 1) % portrait_segments_per_image == 0 时使用 uploaded_portrait_image
其他 Segment：previous_segment_tail_frame，失败时回退 uploaded_portrait_image
```

但回退只能回退到人物图，不能回退到产品或环境空镜。

### 7.3 SRT 时长与 Segment 规划

人物口播的时间轴由脚本、SRT 目标秒数和可选克隆声音语速共同决定：

```text
输入脚本
  -> 按自然句切成 SRT
  -> 按自然句切成源 SRT
  -> 用 HeyGen clone voice + Tempo 真实生成校准音频
  -> 计算 seconds_per_unit
  -> 按单个视频长度合并连续 SRT 为 Dialogue
  -> 生成 1 Shot / 1 Scene / N Dialogue
  -> 每条 Dialogue 对应一个视频 Segment
```

规则：

1. 一个 Dialogue 对应一个视频 Segment；一个 Dialogue 可以包含多条连续 SRT。
2. 不允许为了凑目标秒数而把一句话 / 一条 SRT 切成两个 Segment。
3. 如果单条 SRT 按克隆声音校准后已经超过单个视频长度，必须整句保留并 warning。
3. 如果一句话超过 `srt_target_seconds`，只标记 warning；首版默认不 blocked。
4. 如果一句话明显短于 `srt_target_seconds`，也不自动合并下一句；首版默认不合并。
5. `srt_target_seconds=8` 是 SRT 初始时间轴和质量提示参数，不是断句器，也不是 Scene 拆分器。
6. `portrait_segments_per_image` 控制人物照片首帧重新注入周期，默认建议 `2`。

### 7.4 与普通脚本生成的差异

| 项 | 脚本生成 | 人物口播 |
| --- | --- | --- |
| 来源展示 | 脚本生成 | 人物口播 |
| create_mode | `script` | `person_talking_head` |
| workflow_id | `script` | `person_talking_head_v1` |
| 默认 StoryBoard | quick 或用户选择 | 默认 quick |
| 资源策略 | 可按口播/空镜/产品混合 | talking head only |
| 空镜 | 允许后续 plan 判断 | 禁止自动生成和匹配 |
| 首帧 | 按 VideoPlan 默认策略 | 上传人物照片 + N 段复用 + 上一段尾帧 |
| 口型 | 按 plan 判断 | 默认优先保留 lipsync |
| 阻断条件 | 允许缺部分素材后续补 | 缺人物锚点或声音配置时应 blocked |
| 默认视频模型 | 当前配置 | `wan/wan2.7-r2v` |
| ToolLibrary | Analysis_V1 | `TalkingHead_V1` 的 00 + Analysis_V1 后续工具 |

## 8. 默认模型标准

每个 profile 必须声明模型默认值，但不能把 provider/model 写死在 UI 组件中。

### 8.1 推荐优先级

```text
用户最近选择
> profile 指定 provider/model
> profile preset，例如 max / flash
> 后端返回 default_model
> 可用模型列表第一个
```

### 8.2 人物口播默认建议

人物口播首版建议：

| 模型用途 | 默认策略 |
| --- | --- |
| Prompt Model | `max`，用于生成高质量 SRT Rewrite / StoryBoard prompt。 |
| Run Model | `max`，用于 SRT Rewrite。 |
| StoryBoard Quick | 不调用模型。 |
| VideoPlan | 使用当前视频生成配置，但 profile 要传入 talking-head-only 策略。 |
| Video Execute | 默认 `wan/wan2.7-r2v`，不从普通脚本生成继承当前模型。 |
| TTS / Voice | 默认 HeyGen 克隆声音；缺失时 StoryBoard 生成可继续，一键成片 blocked。 |

人物口播默认视频模型已明确：

```json
{
  "video_provider": "wan",
  "video_model": "wan2.7-r2v"
}
```

注意：Wan RTV 执行链路已在 Analysis V1 中实现。人物口播首版不重新实现视频执行器，只在 VideoPlan 中按现有 Analysis V1 Wan RTV Python 合同准备参数。

## 9. 前端改造清单

### 9.1 必改文件

```text
OpenCrew/frontend/src/modules/koubo/KouboTaskList/KouboTaskCreateMenu.jsx
OpenCrew/frontend/src/modules/koubo/KouboTaskList/KouboTaskListPage.jsx
OpenCrew/frontend/src/modules/koubo/KouboTaskList/KouboTaskCreateFromScriptModal.jsx
OpenCrew/frontend/src/modules/koubo/KouboTaskList/kouboTaskListApi.js
OpenCrew/frontend/src/modules/koubo/KouboTaskList/kouboTaskListStatus.js
OpenCrew/frontend/src/modules/koubo/KouboTaskList/kouboTaskListModel.js
```

### 9.2 建议新增

```text
OpenCrew/frontend/src/modules/koubo/KouboTaskList/kouboWorkflowProfiles.js
OpenCrew/frontend/src/modules/koubo/TalkingHeadV1/TalkingHeadV1Module.jsx
OpenCrew/frontend/src/modules/koubo/TalkingHeadV1/talkingHeadV1Api.js
```

### 9.3 改造要求

1. `KouboTaskCreateMenu` 增加 `人物口播` 按钮。
2. `KouboTaskListPage` 增加 active profile 状态。
3. 点击 `脚本生成` 时传入 script profile。
4. 点击 `人物口播` 时传入 person talking head profile。
5. 弹窗 submit payload 带 `profile_id`。
6. 任务列表 `CREATE_MODE_LABELS` 增加 `person_talking_head: "人物口播"`。
7. 筛选 mode 必须支持新 create_mode。
8. 任务行操作仍保留视频分析和 StoryBoard 入口。
9. `KouboTaskCreateFromScriptModal` 为人物口播 profile 增加人物形象 Tab。
10. 人物形象 Tab 支持上传照片、预览、保存、回填首帧策略。
11. 声音与节奏 Tab 支持 HeyGen voice list、voice clone、voice 选择、Tempo 输入。
12. 人物口播 profile 的模型默认显示为 Wan R2V。
13. 人物口播任务需要有 SRT 工作台页面，形态参考动作模拟 SRT 页面。
14. SRT 工作台页面展示 SRT、Segment 规划、人物照片、声音、Tempo、首帧复用和进入 StoryBoard 的按钮。
15. StoryBoard 工作台页面展示 Shot / Scene / Dialogue / Segment、`dialogue_asset_key`、资源绑定、一键成片入口和状态。
16. 两个页面必须通过 stable id / `dialogue_asset_key` / Segment key 连接资源，不允许依赖显示文本或临时序号。
17. Shell 路由需要识别 `#/talking-head/tasks/{task_id}`，并把它作为 `koubo-task-list` 任务线的一部分。
18. 人物口播 SRT 工作台顶部按钮和动作模拟保持同构：`任务列表 / 重新运行 / 一键成片 / 强制重建 / 故事板 / 折叠`。
19. `KouboTaskListPage` 打开专用流程弹窗时不得清空 `selectedTaskId`；选中 Task 后点击 `人物口播` 应进入该 Task 的编辑态。
20. `KouboTaskCreateFromScriptModal` 必须区分“新建默认值”和“编辑已有值”。已有 Task 的 profile 参数、声音、语速、人物图、首帧复用参数不得被 profile 默认值覆盖。
21. `kouboTaskListApi` 必须为每个可编辑 profile 暴露对应 update 方法，例如 `updateTalkingHead(taskId, payload)`。
22. `kouboTaskListModel` 必须把后端返回的 `profile_id`、`talking_head`、`portrait_image` 等 profile 参数标准化给弹窗使用。

## 10. 后端改造清单

### 10.1 必改文件

```text
OpenCrew/backend/opcrew_backend/koubo/task_list_router.py
OpenCrew/backend/opcrew_backend/koubo/schemas.py
OpenCrew/backend/opcrew_backend/koubo/router.py
```

### 10.2 建议新增

```text
OpenCrew/backend/opcrew_backend/koubo/workflow_profiles.py
```

### 10.3 改造要求

1. `KouboScriptTaskCreatePayload` 增加 `profile_id`。
2. 创建 / 更新脚本任务时按 profile registry 标准化 `create_mode`、`workflow_id`、默认 prompt、默认 quick config。
3. `task_meta.json` schema 升到 `koubo_task_list_meta_0.2`。
4. `serialize_task()` 继续兼容旧 `script` 任务。
5. Analysis V1 `run-to-storyboard` 编排读取 profile 的默认 `storyboard_mode`。
6. 一键成片新增独立 target，不复用 analysis_v1 或 dance_mimic 的 target。
7. VideoPlan 生成时把 `resource_strategy` 写入 plan input / sidecar，执行器按策略阻断空镜 fallback。
8. 每个可编辑 profile 必须有独立 update 接口。人物口播首版为 `PUT /api/koubo-tasks/{task_id}/talking-head`。
9. update 接口必须复用当前 Task / Session / workspace，不得新建 Session。
10. update 接口必须允许从普通 `script` Task 转换为目标 profile，但不得允许把动作模拟等不同任务线误改成人物口播。
11. update 接口必须在没有新上传文件时保留旧文件引用，例如人物口播的 `portrait_image_path`。
12. update 接口必须合并写入 `Variables.json`，不得覆盖 00 已经写入或其它工具需要的 `default_*_config`。
13. update 接口必须在脚本变更时重新生成 `final_srt_frame_items.json`，并清理旧的 rewritten/storyboard/edit 产物；仅修改声音或语速时不得无意义清空脚本。

### 10.4 ToolLibrary 新工具包

推荐英文名：

```text
TalkingHead_V1
```

理由：

1. `TalkingHead` 是行业里对人物口播 / 半身数字人口播最直接的英文表达。
2. 比 `PersonSpeech` 更贴近视频生成语义。
3. 比 `HostPortrait` 更完整，因为它包含脚本、声音、节奏、首帧和视频生成策略。
4. 与现有 `DanceMimic_V1`、`Analysis_V1` 命名风格一致。

备选：

```text
PersonaTalk_V1
HostTalking_V1
PortraitTalk_V1
```

首版建议固定：

```text
OpenCrew/ToolLibrary/TalkingHead_V1/
```

工具包结构建议：

```text
OpenCrew/ToolLibrary/TalkingHead_V1/
  00_PrepareSessionVariables.py
  04_01_SRTRewrite.py
  01_StoryBoardGenerate.py
  02_StoryBoardStructure.py
  03_StoryBoardConfig.py
  README.md
  tool_registry.json
```

其中 `00/04_01/01/02/03` 是人物口播专属工具。后续视频计划、Wan R2V 执行和成片工具复用 Analysis V1：

```text
Analysis_V1/05_01_VideoPlanGenerator.py
Analysis_V1/05_02_VideoPlanExecutor.py
Analysis_V1/06_01_VideoPlanComposer.py
```

`TalkingHead_V1/00` 的职责：

1. 读取人物口播 Task 配置。
2. 校验人物形象照片存在且可读。
3. 校验 HeyGen voice 配置，若只生成 StoryBoard 可 warning，若一键成片则 blocked。
4. 根据脚本和 voice tempo 计算 SRT 时间轴。
5. 写入 `SessionContext/Variables.json`。
6. 写入人物口播 profile 快照。
7. 写入 `SessionOutput/subtitle/final_srt_frame_items.json`。
8. 写入 StoryBoard Quick 参数：`single_shot/single_scene/merge_srt_to_single_video_length/no_sentence_split`。
9. 写入首帧复用参数：`portrait_segments_per_image`。
10. 写入默认视频模型：`wan/wan2.7-r2v`。

`TalkingHead_V1/04_01` 的职责：

1. 如果已经有脚本或 `SessionOutput/subtitle/final_srt_frame_items.json`，直接把脚本台词规范化为 `rewritten_srt_items.json` 和 `rewritten_dialogue.srt`，不要求 `rewrite_final_prompt`。
2. 如果没有脚本/SRT，但有复杂提示词，调用文本模型生成口播 SRT。
3. 如果没有复杂提示词，但有简单提示词，调用文本模型生成口播 SRT。
4. 只有脚本/SRT、复杂提示词、简单提示词都不存在时，才允许 blocked。
5. 人物口播不得因为 `rewrite_prompt.final_prompt` 为空直接阻断；这是普通 Analysis V1 的约束，不适用于该 profile。
6. 生成的每条 SRT 仍然是一句完整对话，不拆句、不插入空镜；是否合并成 Dialogue/Segment 由 `02` 按单个视频长度负责。

`TalkingHead_V1/01` 的职责：

1. 负责“故事版生成”，替代 `Analysis_V1/04_03_StoryBoardQuick.py`，人物口播链路不得再执行普通 `04_03`。
2. 保留 `04_03` 原先“根据 StoryBoard 最终提示词生成故事版结构”的能力，但该能力必须迁移、封装或内联到 `TalkingHead_V1/01_StoryBoardGenerate.py`。
3. 输入为 `rewritten_srt_items.json`、StoryBoard 最终提示词、人物口播 profile 参数。
4. 输出必须是人物口播初始 StoryBoard；即使模型返回多 Shot / 多 Scene，`01` 也不能把该结构原样交给页面。
5. `01` 不能信任大模型自由分镜结果，必须在输出前保留所有 SRT 顺序、文本和 ID，为 `02` 强制分镜提供完整输入。

`TalkingHead_V1/02` 的职责：

1. 负责“故事版分镜生成”，强制把 StoryBoard 规整为 `1 Shot + 1 Scene`。
2. 真实调用 HeyGen 克隆声音和当前 Tempo 生成校准测试音频，计算 `seconds_per_unit`，并记录校准音频、校准文本、单位数、时长和秒/单位。
3. 按 `single_video_length_seconds / srt_target_seconds` 把连续 SRT 合并为 Dialogue/Segment；一个 Dialogue 可以包含多条 SRT，但一条 SRT 不得被拆开。
4. 不能改变 SRT 顺序；单条 SRT 估算超过单个视频长度时必须整句保留并 warning。
5. 必须同时写入页面实际读取的所有 StoryBoard 展示产物，包括 `srt_storyboard.json`、`koubo_storyboard_edit.json` 以及项目中实际使用的派生结构；不得只写一个文件后假设页面会读取它。
6. 验收时必须打开 StoryBoard 页面确认左侧只出现 `shot_001` 和 `scene_001`，不得出现 `shot_001...shot_016`。

`TalkingHead_V1/03` 的职责：

1. 负责“故事版配置”，不得再改变 Shot / Scene / Dialogue 层级；Dialogue 合并只允许发生在 `02`。
2. 按 `portrait_segments_per_image` 安放人物形象首帧：例如值为 `2` 时，第 `1/3/5...` 个 Segment 的 `Image_New` 必须重新放人物形象，第 `2/4/6...` 个 Segment 的 `Image_New` 必须保持空槽位。
3. 第 `2/4/6...` 个 Segment 只在 `talking_head` / `video_plan` 中记录“后续由 `05_02` 用上一段尾帧覆盖”的策略，不得在配置阶段写入不存在的 `TailFrame.png` 路径。
4. 逐个合并后的 Dialogue 调用 HeyGen 克隆声音生成最终音频，Tempo 必须参与调用。
5. 最终音频生成后，以最终 wav 的真实时长覆盖 Dialogue `duration/start/end`；如果需要保留源 SRT timing，按 Dialogue 内各 SRT 的估算权重分配并同步回 `rewritten_srt_items.json` / `final_srt_frame_items.json`。
6. 每个 Dialogue 都必须生成或绑定一个实际音频产物，并写入 `Audio_Final` / planned audio / duration 字段。
7. 每个合并后的 Dialogue 的 duration 必须来自最终 HeyGen 克隆声音音频真实时长，而不是固定 8 秒、12 秒或简单按字数估算；源 SRT timing 如需展示，只能由 Dialogue 内权重分配回填。
8. 音频生成失败时必须明确 blocked 或 warning 策略；不能只留下空 Audio 槽位冒充已配置。

`TalkingHead_V1/00` 不做：

1. 不调用视频模型。
2. 不生成 StoryBoard。
3. 不调用 HeyGen clone；clone 是创建弹窗或声音 Tab 的动作。
4. 不断句拆分超长句。

专属工具 CLI 兼容要求：

1. 所有由后端执行器调用的 profile 专属工具都必须支持 `--workspace`。
2. 所有可参与后台运行链路的工具都必须支持 `--force`、`--resume`、`--print-json`。
3. 如果工具当前没有增量续跑逻辑，也必须接受 `--resume` 并安全忽略。
4. 输出 JSON 时必须在 `status` 中明确 `completed` / `blocked` / `failed`，不得只依赖进程异常。
5. 新增工具进入 `tool_registry.json` 后，必须用 `python tool.py --help` 验证通用参数存在。

Session Variables / `00` 分发要求：

1. `00_PrepareSessionVariables.py` 不是全局唯一工具名，而是每个 workflow profile 的专属变量装载器。
2. 所有“重新运行 00”“刷新 Session Variables”“查看变量前重建变量”的 UI 入口，必须先识别当前 Task 的 `workflow_id / workflow_mode / profile_id / create_mode / task_meta.json`，再选择对应工具包。
3. 动作模拟必须运行 `ToolLibrary/DanceMimic_V1/00_PrepareSessionVariables.py`；人物口播必须运行 `ToolLibrary/TalkingHead_V1/00_PrepareSessionVariables.py`；普通 Analysis / 脚本生成才运行 `ToolLibrary/Analysis_V1/00_PrepareSessionVariables.py`。
4. 前端不得用 `if not DanceMimic then Analysis 00` 这类二分逻辑；应调用后端统一分发接口，由后端读取 DB、workspace 和 `task_meta.json` 后决定。
5. 任何刷新变量动作运行结束后，必须展示真实返回的 `SessionContext/Variables.json`，并用回归检查确认 profile 专属字段没有被普通 Analysis 默认值覆盖。

## 11. 工具与产物合同

### 11.1 不变产物

所有 profile 都必须继续写：

```text
SessionOutput/subtitle/source_script.txt
SessionOutput/subtitle/final_srt_frame_items.json
SessionContext/Variables.json
SessionOutput/subtitle/rewritten_srt_items.json
SessionOutput/storyboard/srt_storyboard.json
SessionOutput/storyboard/koubo_storyboard_edit.json
```

### 11.2 Profile 产物扩展

建议在 Variables 中增加：

```json
{
  "workflow_profile": {
    "profile_id": "person_talking_head_v1",
    "workflow_id": "person_talking_head_v1",
    "create_mode": "person_talking_head",
    "display_source_label": "人物口播",
    "resource_strategy": {
      "kind": "talking_head_only",
      "allow_cutaway": false
    }
  },
  "talking_head": {
    "portrait": {
      "source": "upload",
      "portrait_image_path": "SessionInput/talking_head/portrait_images/<file>.png",
      "portrait_image_original_path": "/Users/.../.opencrew/talking_head_v1/portrait_images/<file>.png",
      "role": "host_portrait_first_frame"
    },
    "voice_timing": {
      "provider": "heygen",
      "voice_id": "",
      "voice_label": "",
      "tempo": 1.0,
      "duration_estimation": "voice_tempo",
      "seconds_per_char": 0.22
    },
    "segment_planning": {
      "shot_policy": "single_shot",
      "scene_policy": "single_scene",
      "segment_policy": "merge_srt_to_single_video_length",
      "srt_target_seconds": 8.0,
      "portrait_segments_per_image": 2,
      "allow_sentence_split": false
    },
    "video_model": {
      "provider": "wan",
      "model": "wan2.7-r2v",
      "executor": "analysis_v1_wan_rtv"
    }
  }
}
```

建议在 StoryBoard 中增加：

```json
{
  "workflow_id": "person_talking_head_v1",
  "source_type": "person_talking_head_script",
  "resource_strategy": {
    "kind": "talking_head_only",
    "allow_cutaway": false
  },
  "portrait": {
    "source": "upload",
    "portrait_image_path": "SessionInput/talking_head/portrait_images/<file>.png",
    "role": "host_portrait_first_frame",
    "portrait_segments_per_image": 2
  },
  "voice_timing": {
    "provider": "heygen",
    "voice_id": "",
    "tempo": 1.0
  },
  "video_model": {
    "provider": "wan",
    "model": "wan2.7-r2v"
  }
}
```

每条 Dialogue / Segment 需要补充：

```json
{
  "dialogue_asset_key": "dak_0001",
  "segment_index": 1,
  "talking_head": {
    "is_portrait_anchor_segment": true,
    "portrait_anchor_index": 1,
    "first_frame_policy": "uploaded_portrait_image",
    "first_frame_image_path": "SessionInput/talking_head/portrait_images/<file>.png",
    "voice_id": "",
    "tempo": 1.0
  }
}
```

当 `portrait_segments_per_image=2` 时：

| Segment | `is_portrait_anchor_segment` | `first_frame_policy` |
| --- | --- | --- |
| 1 | true | `uploaded_portrait_image` |
| 2 | false | `Image_New` 空槽位；`video_plan.first_frame_strategy=previous_segment_tail_frame` |
| 3 | true | `uploaded_portrait_image` |
| 4 | false | `Image_New` 空槽位；`video_plan.first_frame_strategy=previous_segment_tail_frame` |

### 11.3 VideoPlan 合同

人物口播 VideoPlan 每个 task 至少需要显式记录：

```json
{
  "target_asset_key": "dak_0001",
  "resource_strategy": "talking_head_only",
  "allow_cutaway": false,
  "video_provider": "wan",
  "video_model": "wan2.7-r2v",
  "first_frame": {
    "source_type": "uploaded_portrait_image",
    "image_path": "SessionInput/talking_head/portrait_images/<file>.png",
    "fallback_source_type": "previous_segment_tail_frame",
    "fallback_applied_by": "05_02"
  },
  "executor": "analysis_v1_wan_rtv",
  "voice": {
    "provider": "heygen",
    "voice_id": "",
    "tempo": 1.0
  },
  "need_lipsync": true,
  "sync_mode": "lipsync",
  "blocked_reasons": []
}
```

如果无法满足人物口播资源策略，不允许静默降级成空镜，必须：

```json
{
  "status": "blocked",
  "blocked_reasons": [
    {
      "code": "person_reference_missing",
      "message": "人物口播需要人物参考图或已确认的人物形象资源。"
    },
    {
      "code": "heygen_voice_missing",
      "message": "人物口播一键成片需要选择 HeyGen 克隆声音或可用声音。"
    }
  ]
}
```

VideoPlan 生成器可以复用 Analysis V1，但必须读取 profile 后改变默认策略：

1. 默认模型写成 `wan/wan2.7-r2v`。
2. 禁止生成空镜 task。
3. 首帧按 `portrait_segments_per_image` 周期放置上传人物图。
4. 非锚点 Segment 优先使用上一段尾帧。
5. 执行器复用 Analysis V1 已实现的 Wan RTV Python，不在人物口播流程中另起一套视频执行逻辑。
6. Wan R2V 所需模块 reference video 由 `05_02_VideoPlanExecutor.py` 自动复制和带入；不要把它设计成用户上传字段，也不要用 `openclip_tasks.reference_video_path` 校验它。

## 12. 验收标准

### 12.1 创建入口

1. 任务列表顶部 `脚本生成` 右侧出现 `人物口播`。
2. 点击 `人物口播` 打开与脚本生成一致的创建弹窗，并新增 `人物形象`、`声音与节奏` Tab。
3. 默认字段、默认模型、默认 StoryBoard 参数来自人物口播 profile。
4. `人物形象` Tab 可上传人物照片，保存后可预览并写入 task meta。
5. `声音与节奏` Tab 可选择 HeyGen 克隆声音和 Tempo。
6. 保存后任务列表来源显示 `人物口播`。
7. 任务 workspace 里 `task_meta.json.profile_id=person_talking_head_v1`。
8. 任务 workspace 里记录 `video_model=wan/wan2.7-r2v`。
9. 选中已有 Task 后点击 `人物口播`，弹窗显示该 Task 的已保存参数，而不是人物口播默认值。
10. 选中已有 Task 后点击保存，仍更新同一个 `task_id`，不新增 Task / Session。
11. 保存后关闭并重新打开该 Task，人物图、声音、Tempo、单个视频长度、首帧覆盖视频个数仍保持保存值。
12. 不重新上传人物图时保存其它字段，原人物图引用必须保留。

### 12.2 StoryBoard 生成

1. 人物口播任务可以生成 `srt_storyboard.json`。
2. 每个 Dialogue 有唯一稳定 `dialogue_asset_key`。
3. StoryBoard 顶层记录 `workflow_id=person_talking_head_v1`。
4. StoryBoard 顶层或 meta 记录 `resource_strategy.kind=talking_head_only`。
5. StoryBoard 顶层或 meta 记录上传人物照片、HeyGen voice 和 Tempo。
6. 第一条 SRT 的 `Image_New` 使用上传人物照片。
7. StoryBoard 结构为 `1 Shot / 1 Scene / N Segment`。
8. 每条 SRT 对应一个 Dialogue / Segment。
9. StoryBoard 生成过程不得为了凑 8 秒自动断句。
10. 生成 StoryBoard 时不执行 Wan RTV，只准备后续 VideoPlan 所需的首帧、声音、Segment key 和模型参数。

### 12.3 一键成片

1. `一键成片` 入口只打开面板，不自动运行。
2. 面板内启动后写入独立 target 状态。
3. 缺少人物参考图或声音配置时 blocked。
4. VideoPlan 不自动生成空镜任务。
5. 每段视频的 first frame 来源符合人物口播策略。
6. 逐段状态从真实 plan / execution / compose 文件读取。
7. 支持从 `05_02` 或 `05_01` 局部续跑。
8. Video Execute 默认使用 `wan/wan2.7-r2v`。
9. Wan RTV 执行复用 Analysis V1 现有 Python，人物口播只验证 VideoPlan 参数是否完整。
10. 当 `portrait_segments_per_image=2` 时，第 1、3、5... 段必须重新注入上传人物照片。
11. 音频默认使用 HeyGen voice + Tempo 生成，不允许静音成片。

### 12.4 回归风险

1. 旧 `脚本生成` 任务仍显示为 `脚本生成`。
2. 旧 `dance_mimic` 任务仍显示为 `动作模拟`。
3. 旧 `script` 任务的 `task_meta.json` 不需要迁移即可打开。
4. 现有 `run-to-storyboard` 对普通脚本任务行为不变。
5. StoryBoard 资源绑定仍以 `dialogue_asset_key` 为准。
6. 点击专用流程按钮不得丢失当前任务选择。
7. 编辑已有 profile Task 时不得用 profile 默认值覆盖数据库已有参数。
8. 前端保存入口不得只实现 create；必须按是否有 `task_id` 分流 create/update。
9. 后端 update 不得只更新数据库而漏写 workspace meta / variables，否则 00 和后续工具会读到旧参数。

## 13. 人物口播首版确认状态

当前已确认：

| 项 | 结论 |
| --- | --- |
| ToolLibrary 英文名 | 固定为 `TalkingHead_V1`。 |
| 按钮位置和名称 | 放在 `脚本生成` 右侧，按钮名 `人物口播`。 |
| SRT 工作台路由 | 使用 `#/talking-head/tasks/{task_id}`，页面形态参考动作模拟。 |
| Wan RTV 执行器 | 复用 Analysis V1 现有 Python，不重新实现。 |
| 结构规划 | 固定 `1 Shot / 1 Scene / N Segment`，不按 8 秒拆 Scene。 |
| Segment 粒度 | 多条连续 SRT 可合并成一个 Dialogue/Segment，合并上限是单个视频长度。 |
| SRT 秒数 | `srt_target_seconds` / 单个视频长度是单个 Dialogue/Segment 的目标上限，默认参考动作模拟使用 `8`。 |
| 超长句处理 | 用户会预留空间，理论上不会超过 8 秒；如果超过只 warning，不 blocked。 |
| 声音选择 | 创建任务时非必选；可以后续在 StoryBoard 页面选择 HeyGen voice 和 Tempo。 |
| 默认视频模型 | `wan/wan2.7-r2v`。 |

仍可后续细化但不阻塞首版实现：

| 项 | 首版默认 |
| --- | --- |
| 创建弹窗 Tab | `脚本配置 / 人物形象 / 声音与节奏`。 |
| 人物照片上传路径 | `~/.opencrew/talking_head_v1/portrait_images/`，workspace 内落到 `SessionInput/talking_head/portrait_images/`。 |
| 默认 Prompt Model | profile preset `max`。 |
| 默认 Run Model | profile preset `max`。 |
| SRT 时间轴来源 | 普通脚本先按参数初始化；如已选 HeyGen voice + Tempo，`02` 用真实校准音频估算并合并 Dialogue，`03` 用最终音频真实时长覆盖。 |
| 默认 Tempo | `1.0`，后续 StoryBoard 可调整。 |
| 首帧复用参数 | `portrait_segments_per_image=2`，首版可作为参数保存。 |
| 锚点段规则 | 第 1、3、5... 段重新注入人物照片；非锚点段优先使用上一段尾帧。 |
| 是否允许产品同框 | 首版允许人物 + 产品同框，但不允许纯产品空镜。 |
| 是否允许背景变化 | 首版允许轻微变化，人物身份必须连续。 |
| 一键成片 target | `person_talking_head_v1_one_click_movie`。 |

## 14. 实现顺序建议

1. 增加 profile registry 和 `人物口播` 按钮。
2. 复用脚本创建弹窗，并按 profile 增加 `人物形象`、`声音与节奏` Tab。
3. 增加人物照片上传能力，路径参考动作模拟但使用 `talking_head_v1/portrait_images` 独立目录。
4. 增加 `TalkingHeadV1` SRT 工作台路由和页面，形态参考动作模拟任务详情页。
5. 复用现有 HeyGen voice list / clone 能力；创建时可选，StoryBoard 页面可后补。
6. 让创建任务写入 `profile_id/create_mode/workflow_id/resource_strategy/portrait/voice_timing/segment_planning/video_model`。
7. 新建 `OpenCrew/ToolLibrary/TalkingHead_V1/00_PrepareSessionVariables.py`。
8. 新建 `OpenCrew/ToolLibrary/TalkingHead_V1/04_01_SRTRewrite.py`，按“脚本/SRT > 复杂提示词 > 简单提示词”的优先级准备口播 SRT。
9. 新建 `OpenCrew/ToolLibrary/TalkingHead_V1/01_StoryBoardGenerate.py`，替代 `Analysis_V1/04_03`，保留最终提示词生成故事版结构能力。
10. 新建 `OpenCrew/ToolLibrary/TalkingHead_V1/02_StoryBoardStructure.py`，强制 `1 Shot + 1 Scene`，并用 HeyGen 克隆声音校准结果按单个视频长度合并 SRT 为 Dialogue/Segment。
11. 新建 `OpenCrew/ToolLibrary/TalkingHead_V1/03_StoryBoardConfig.py`，按首帧复用参数安放人物图，并逐个合并后的 Dialogue 调用 HeyGen 克隆声音生成最终音频。
12. 让人物口播 StoryBoard 链路运行 `TalkingHead_V1/00 + TalkingHead_V1/04_01 + TalkingHead_V1/01 + TalkingHead_V1/02 + TalkingHead_V1/03`，不得执行 `Analysis_V1/04_03`。
13. 在 StoryBoard / Variables 中透传人物照片、声音、Tempo、SRT 秒数参数、首帧复用参数和逐句音频时长。
14. 调整 VideoPlan 生成，让人物口播默认 `wan/wan2.7-r2v`、talking-head-only、禁止空镜 fallback。
15. 增加人物口播独立一键成片 target。
16. 最后做局部续跑、状态面板、阻断原因展示和普通脚本生成回归测试。

这个顺序能先把产品入口和任务身份立住，再逐步收敛后半段生成策略，避免一开始把 UI、Task、StoryBoard、VideoPlan、Composer 全部耦合在同一次改造里。

## 15. 踩坑记录：选中 Task 后参数保存

### 15.1 问题现象

用户在任务列表中选中一个 Task 后，点击 `人物口播` 按钮打开弹窗，修改人物图、声音、Tempo、单个视频长度或首帧覆盖视频个数并点击保存。再次选中同一个 Task 打开弹窗时，参数没有保存回来，或者弹窗显示人物口播默认值。

这类问题对后续 workflow profile 是高风险问题，因为它会让用户误以为“保存成功”，但后续 00 / StoryBoard / 一键成片读取的仍是旧参数。

### 15.2 根因模式

| 根因 | 具体表现 | 后果 |
| --- | --- | --- |
| 点击 profile 按钮时清空 `selectedTaskId` | 打开弹窗前执行 `setSelectedTaskId(null)` | 保存被当成新建，无法更新当前 Task。 |
| 弹窗只按默认 profile 初始化 | `formFromTask()` 没有读取 `task.talkingHead` / `storyboardQuickConfig` | 已保存人物图、声音、Tempo、分段参数回不来。 |
| 默认值覆盖编辑值 | 编辑已有 Task 时仍写入 `TALKING_HEAD_QUICK_CONFIG`、`tempo=1`、`portrait_segments_per_image=2` | 用户保存过的参数被默认值覆盖。 |
| 前端只有 create 接口 | 保存始终调用 `POST /create-talking-head` | 选中 Task 保存后新增 Task，而不是更新当前 Task。 |
| 后端没有 profile update 路由 | 前端 update 请求无落点 | 保存失败或被错误吞掉。 |
| 后端只更新数据库 | 没同步 `task_meta.json` / `Variables.json` | 任务列表看似更新，00 和工具链仍读旧参数。 |
| 更新时不保留旧上传文件 | 未上传新图就把 `portrait_image_path` 写空 | 修改声音或 Tempo 会丢人物首帧。 |
| 只按 step id 判断工具依赖 | 人物口播的 `01_StoryBoardGenerate` 也叫 `01`，但普通 Analysis V1 的 `01` 是 `01_VideoProbeMetadata` | 无 task-level analysis reference video 的人物口播运行被误判为普通视频分析任务，报 `Task-level analysis reference video path is required before running video-dependent Analysis_V1 steps`。 |
| 混淆两种 reference video | 把 Wan R2V 自动带入的模块 reference video 当成用户任务入口 reference video | 误以为人物口播必须上传参考视频，或错误地用 `reference_video_path` 阻断 StoryBoard / 一键成片启动。 |
| 复用 Analysis V1 的 SRT 改写阻断 | 人物口播有脚本，但 `rewrite_prompt.final_prompt` 为空时被 `04_01` 阻断 | 明明可以直接使用脚本，却无法进入 StoryBoard。 |
| 只按普通 Analysis 输入依赖检查 `04_01` | 提示词生成口播时还要求 `final_srt_frame_items.json` 已存在 | “无脚本，用提示词生成”这条入口被提前挡住，专属 `04_01` 无法执行。 |
| 打开运行弹窗时自动启动 | 顶部 `重新运行` / `StoryBoard生成` 按钮同时执行 `setRunDialogOpen(true)` 和 `startRun()` | 用户只是想查看状态或确认步骤，却已经产生新的 attempt 并消耗模型/工具运行成本。 |
| 打开运行弹窗不加载计划步骤 | 只有点击运行后才出现步骤，打开弹窗时显示空白或“尚未运行” | 用户无法在执行前确认这次到底会跑哪些工具，容易误触发或误以为流程没有配置好。 |
| 专属工具不兼容执行器通用参数 | 后端在非 force 模式传入 `--resume`，但 profile 专属工具未声明该参数 | 工具在 argparse 阶段直接退出，业务上看起来像“计算 Shot/Scene/Dialogue 阻断”。 |
| 完成步骤暴露说明小字 | `04_01` 完成后在步骤行里显示 `talking_head_script_passthrough...` 这类内部说明 | 运行面板显得凌乱，用户无法快速扫描主流程状态。 |
| 步骤行缺少顺序标签 | 运行面板只展示步骤名称，没有 `1/2/3...` | 用户无法快速确认任务执行顺序，尤其是一键成片长链路中容易看乱。 |
| 误信普通 `04_03` 输出 | 人物口播先跑 `Analysis_V1/04_03`，再试图用 `01/02` 修正 | 大模型自由分镜可能生成 16 个 Shot / 16 个 Scene，破坏人物口播固定结构。 |
| 只写 `srt_storyboard.json` 不写页面实际读取产物 | 后端以为结构已规整，但 StoryBoard 页面仍读旧的 edit / 派生结构 | 页面继续显示多 Shot / 多 Scene，用户看到的不是工具实际想表达的结构。 |
| 把声音配置当成声音生成 | `03` 只写 voice_id、planned_path 或空 Audio 槽位，没有真实调用 HeyGen | 无法用克隆声音真实时长计算每句 duration，也无法进入可成片状态。 |
| 混淆校准音频和最终音频 | `02` 没有真实生成克隆声音校准音频，或 `03` 没有按合并后的 Dialogue 生成最终音频 | 单个视频长度无法正确合并 SRT，页面仍显示 8 秒/12 秒固定值或 16 个 Dialogue。 |
| 未验证首帧复用落点 | 只在内部变量里写 `portrait_segments_per_image`，没有检查第 3/5/... 个 Segment 的 `Image_New` | 用户设置“1 张图管 2 个 Segment”后，第三个 Segment 没有重新放人物图。 |
| 提前写入不存在的尾帧路径 | 第 `2/4/6...` 个 Segment 在 `03` 配置阶段把 `Image_New.path` 写成上一段 `TailFrame.png` | 尾帧尚未由 `05_02` 生成，StoryBoard 页面显示无真实链接的坏图片；正确做法是 `Image_New` 空着，仅在 `video_plan` 记录后续尾帧覆盖策略。 |
| 对实现差异不主动确认 | “生成声音”或“04_03 能力保留但不执行”存在实现方式选择，却直接按预留槽位或复用旧工具实现 | 需求虽然有方向，但关键执行边界被实现者误判，造成返工。 |
| 为专属 profile 修改通用 Analysis 工具 | 为了满足人物口播逐句 Segment、首帧复用或声音配置，直接改 `Analysis_V1/05_01` 等通用工具 | 影响其它工作流，破坏“专属差异沉淀在 profile 工具包、通用工具稳定复用”的资产边界。 |
| Session Variables 刷新跑错 `00` | StoryBoard 顶部刷新变量按钮只特判 DanceMimic，其余统一调用普通 `Analysis_V1/00` | 人物口播 `Variables.json` 被覆盖为旧的 `one_srt_one_segment` / 普通模型配置，后续 `02/03/05` 消费到错误参数。 |
| 把 StoryBoard 的 Video Plan 执行器按 workflow 分流 | `video_plan_execution_script_path()` 看到 `person_talking_head_v1` 就把 Analysis 05_01 plan 交给 `TalkingHead_V1/05_02` | StoryBoard 报 `Lip-sync is required but disabled`，或因 Prompt fallback 不同报 `VIDEO_OPENROUTER_POSITIVE_BASE` 缺失。StoryBoard 除“刷新 Session Variables”和“重新加载提示词”外全部属于 Analysis_V1；TalkingHead 05_02 只用于人物口播一键成片。 |
| 一键成片音频绿灯只看 `SegmentAudio_Final` | 03 已真实生成每个 Dialogue 的 HeyGen `Audio_Final`，但 05_02 尚未为所有 Segment 产出 `SegmentAudio_Final`，状态面板仍显示白色或等待 | 用户误以为音频没有生成；正确口径是口播 `Audio_Final` / `dialogue_audio_tasks` / `sync_mode=lipsync` 均可证明“音频已匹配”，`SegmentAudio_Final` 只表示 05_02 阶段可消费的合成音频。 |
| StoryBoard TTS 面板吃旧 selection | 创建页已选 HeyGen voice / Tempo，但 `koubo_storyboard_edit.json.storyboard_tts_selection` 仍是旧 Qwen/Cherry，Timing 面板优先读取旧值 | 人物口播必须从 `task.storyboard_quick_config_json`、`meta.storyboard_quick_config`、`Variables.json.talking_head.voice_timing` 恢复 HeyGen selection，并写回 `storyboard_tts_selection`；推荐声音后必须有独立 Tempo 参数并与当前推荐音色同步；仅修改 Tempo 保存时必须保留当前 provider/model/voice/candidate。 |
| StoryBoard Timing 保存只写 edit plan | 点击保存后 `koubo_storyboard_edit.json` 更新了，但 `Variables.json` / `task_meta.json` 仍是旧 voice/Tempo | 对人物口播而言，Timing 面板保存不是纯 StoryBoard UI 设置，必须同步 `storyboard_tts_selection`、`SessionContext/Variables.json.talking_head.voice_timing`、`SessionOutput/task_list/task_meta.json.talking_head.voice_timing` 和 `default_voice_clone_config`。 |
| TTS 已生成但接口卡在“生成中” | `scene-tts/events` 已调用 HeyGen 并写出 wav，但回写 StoryBoard 时触发 `storyboard_source_changed`，流式响应异常中断 | TTS 回写属于后端受控修改，应先基于当前 `srt_storyboard.json` 重新应用 source signature，再保存 edit/source，不能拿旧签名阻断自己刚写出的音频。 |
| TTS 只更新前端显示时长 | 页面读取 `tts_manifests/*_Audio_Final.json` 后显示真实时长，但 `koubo_storyboard_edit.json` / `srt_storyboard.json` 仍是旧估算时长 | 生成接口完成或命中锁定缓存时，都必须把 `duration_seconds` 回写到 Dialogue，并通过 recalculate 更新 Scene/Shot；加载 StoryBoard 时也要用 manifest duration 覆盖旧估算值。 |

### 15.3 标准修复方式

以后实现任意新 workflow profile 时，必须按以下方式落地：

1. 前端列表页保留当前选中 Task，profile 按钮只设置 `activeProfile` 并打开弹窗。
2. 弹窗 `formFromTask(task)` 必须完整映射 profile 参数。
3. 弹窗进入编辑态时，只允许补缺省值，不允许用 profile 默认值覆盖已有值。
4. 提交入口按 `taskId` 分流：有 `taskId` 调 update，没有 `taskId` 调 create。
5. 后端实现该 profile 的 update 路由，并复用当前 Task / Session / workspace。
6. update 路由同时写数据库、`task_meta.json`、`Variables.json` 和必要的输入文件。
7. 文件类字段采用“有新文件则替换，无新文件则保留旧引用”的策略。
8. 如果脚本变更，才清理依赖脚本的下游产物；只改声音、Tempo 或首帧策略时不得误删已保存输入。
9. 后端依赖校验必须是 profile-aware：不能只看 `01`、`02` 这类 step id，要结合 `workflow_profile` / tool script 判断该步骤是否真的依赖 task-level analysis reference video。
10. 专用流程前端应调用专用语义接口，例如 `/api/talking-head-v1/tasks/{task_id}/run-storyboard`，由后端强制注入 `person_talking_head_v1`，不要让页面直接裸调普通 Analysis V1 路径。
11. Wan R2V reference video 只在 05_02 执行阶段由执行器自动带入；任何创建 / StoryBoard / 运行启动校验都不应要求用户提供它。
12. 如果新 profile 的脚本生成入口规则不同，必须提供该 profile 的专属 `04_01` 或等价步骤，不得直接复用 Analysis V1 的强制 `rewrite_final_prompt` 校验。
13. profile-aware 依赖检查必须允许专属 `04_01` 自己处理脚本/SRT/提示词 fallback，不能在执行计划阶段提前按普通 Analysis V1 的输入文件阻断。
14. 运行类入口必须分离“打开面板”和“启动后台任务”：页面按钮只打开 `RunProgressDialog` / 一键成片面板；面板内主按钮才调用 `runStoryboard()`、`oneClickMovie()` 或其它启动接口。
15. 打开运行面板时只能读取最新状态和展示步骤，不得新建 attempt、不得改变任务状态、不得触发模型调用。
16. 打开运行面板时必须加载 profile 的计划步骤 `planSteps`；没有历史 attempt 时展示 `pending` 状态的计划步骤，有运行中 attempt 时展示真实步骤状态。
17. StoryBoard 编辑页的 workflow-aware 分流必须使用白名单：只允许“刷新 Session Variables”和“重新加载提示词”。不得用 `workflow_id` 替换 Video Plan、Image Plan、Video Only Plan、Composer、素材绑定或尾帧物化的 Analysis_V1 工具。
18. StoryBoard Video Plan 执行器必须固定为 `Analysis_V1/05_02_VideoPlanExecutor.py`；人物口播一键成片才允许运行 `TalkingHead_V1/05_02_VideoPlanExecutor.py`。
19. 每个 profile 专属工具上线前必须执行 `--help` 和一次带 `--resume --print-json` 的命令级验证，确保后端通用执行器不会因为参数不兼容中断。
20. 运行面板步骤行的信息密度必须统一：非异常状态不显示 `step.message` 小字；异常状态才显示错误原因，正常 warning 进入日志或详情，不进入主步骤列表。
21. 运行面板步骤行必须有顺序标签；共享组件默认渲染，profile 页面不得关闭或隐藏该标签。
22. 人物口播 StoryBoard 链路不得执行普通 `Analysis_V1/04_03`；如果需要其最终提示词生成结构能力，必须迁移到 `TalkingHead_V1/01`。
23. 结构规整工具必须写入页面真实读取的全部 StoryBoard 产物；实现前必须确认页面读哪个文件，不能凭猜测只写一个 JSON。
24. 任何“生成声音”“测算声音时长”“安放首帧到新图”这类会影响可见产物的需求，都必须通过真实页面或产物检查验证，不得只写配置字段。
25. 对存在两种实现方式的需求，必须先向用户确认，例如“真实调用 HeyGen 生成音频”还是“只预留音频槽位”；未确认时不得按低成本预留实现代替真实生成。
26. profile 专属差异必须优先落到该 profile 的 ToolLibrary，例如 `TalkingHead_V1/01/02/03`；不得为了单个客户流程修改 `Analysis_V1/05_01/05_02/06_01` 这类通用工具，除非用户明确要求升级通用能力。
27. 人物口播对 `05_01` 的要求只能通过上游 StoryBoard 产物表达：`03` 必须把每个合并后的 Dialogue 的音频、最终真实时长、`Image_New`、`video_plan.segment_policy=merge_srt_to_single_video_length`、`force_single_segment=true` 等配置写完整，让通用 `05_01` 读取，不在 `05_01` 中写人物口播特判。
28. 不能把“逐句 SRT”误当成“逐个 Segment”。人物口播的 `单个视频长度` 是 Dialogue/Segment 合并上限；`02` 必须用克隆声音校准结果把多条连续 SRT 合并成 Segment，`03` 再用最终音频真实时长覆盖估算值。
29. HeyGen 调用除 API Key 外，所有任务级参数都必须从 `SessionContext/Variables.json` 读取：`voice_id`、`voice_label`、`tempo`、provider/model、`api_key_ref`。`task_meta.json` 只能作为兼容回退；正式链路必须先跑 `00` 准备 Variables。
30. 所有 Session Variables 刷新入口必须调用后端 workflow-aware 分发接口，不能由前端直接调用普通 `Analysis_V1` 的 `run_only_step=00`；验收时至少检查 DanceMimic、TalkingHead、普通 Analysis 三类 Task 分别运行对应工具包的 `00`。
31. 一键成片逐句状态的音频完成态必须按 profile adapter 判断：人物口播先看 `dialogue_audio_tasks[].existing_audio_path` / StoryBoard `working_assets.audio.path` / `Audio_Final`，再看 `SegmentAudio_Final`；如果运行摘要只返回 `lipsync.sync_mode=lipsync` 和 `reason=dialogue_marked_talking_head`，前端也应显示为 `音频已匹配`，不能等待 05_02 把 segment 音频合成完才变绿。
32. StoryBoard TTS 接口的验收不能只看 wav 文件存在；必须同时检查 SSE 返回 `completed`、无 409/500、manifest 存在、StoryBoard audio path 已写回、Dialogue/Scene/Shot duration 与最终 wav 时长一致。
33. TTS 缓存命中不是“只读优化”。如果缓存 manifest 存在但 StoryBoard 仍是旧 duration，缓存路径也必须触发后端同步或加载态覆盖，避免后续 05_01/05_02 消费旧时长。

### 15.4 回归用例

| 用例 | 操作 | 期望 |
| --- | --- | --- |
| TH-SAVE-01 | 选中普通脚本 Task，点人物口播，保存 | 同一 `task_id` 转为人物口播，任务列表不新增 Task。 |
| TH-SAVE-02 | 选中人物口播 Task，修改 Tempo 为 `1.4` 并保存 | 重新打开弹窗后仍为 `1.4`，`Variables.json.talking_head.voice_timing.tempo=1.4`。 |
| TH-SAVE-03 | 上传人物图并保存，再只改声音保存 | 人物图路径不丢，第一条 SRT 的新图仍指向该人物图。 |
| TH-SAVE-04 | 修改单个视频长度为 `6` 并保存 | 重新打开后单个视频长度为 `6`，`segment_planning.srt_target_seconds=6`。 |
| TH-SAVE-05 | 修改首帧覆盖视频个数为 `3` 并保存 | 重新打开后仍为 `3`，尾帧策略 `reset_every_segments=3`。 |
| TH-SAVE-06 | 保存后运行 `TalkingHead_V1/00` | 00 从当前 Task 的 `Variables.json` / meta 读取最新人物口播参数。 |
| TH-SAVE-07 | 人物口播 Task 无 task-level analysis reference video，点击运行 StoryBoard | 后端识别 `person_talking_head_v1`，执行 `TalkingHead_V1/00 + TalkingHead_V1/04_01 + TalkingHead_V1/01 + TalkingHead_V1/02 + TalkingHead_V1/03`，不得执行 `Analysis_V1/04_03`，不得报 task-level reference video 缺失。 |
| TH-SAVE-08 | 人物口播进入 05_02 Wan R2V 视频执行 | 执行器自动复制并带入 `Video_Wan_R2V.mp4`，同时使用 Segment 的首帧 / 尾帧；不要求用户上传 reference video。 |
| TH-SAVE-09 | 人物口播 Task 已有脚本，但 `rewrite_final_prompt` 为空，点击运行 StoryBoard | `TalkingHead_V1/04_01` 直接使用脚本生成 rewritten SRT，不得报 `rewrite_final_prompt_missing`。 |
| TH-SAVE-10 | 人物口播 Task 没有脚本，有复杂提示词，点击运行 StoryBoard | `TalkingHead_V1/04_01` 使用复杂提示词生成 SRT，再进入 StoryBoard。 |
| TH-SAVE-11 | 人物口播 Task 没有脚本、没有复杂提示词，但有简单提示词，点击运行 StoryBoard | `TalkingHead_V1/04_01` 使用简单提示词生成 SRT，再进入 StoryBoard。 |
| TH-SAVE-12 | 人物口播 Task 点击顶部 `重新运行` 按钮 | 只打开运行弹窗，不新建 attempt，不启动 00 / 04_01 / 01 / 02 / 03；必须点击弹窗蓝色运行按钮才开始运行。 |
| TH-SAVE-13 | 人物口播 Task 点击 `一键成片` 按钮 | 只打开一键成片面板，不启动 05_01 / 05_02 / 06_01；必须点击面板主按钮才开始运行。 |
| TH-SAVE-14 | 人物口播 Task 打开 `重新运行` 弹窗但不点击蓝色运行按钮 | 弹窗立即展示 `00 / 04_01 / 01 / 02 / 03` 的待执行任务清单，所有步骤为等待状态。 |
| TH-SAVE-15 | 人物口播 Task 打开 `一键成片` 面板但不点击主按钮 | 面板立即展示完整待执行任务清单，包含 `00 / 04_01 / 01 / 02 / 03 / 05_01 / 05_02 / 06_01`。 |
| TH-SAVE-16 | 后端以默认非 force 模式运行 `TalkingHead_V1/01_StoryBoardGenerate.py` | 工具接受 `--resume` 参数，不得报 `unrecognized arguments: --resume`。 |
| TH-SAVE-17 | 后端以默认非 force 模式运行 `TalkingHead_V1/02_StoryBoardStructure.py` / `03_StoryBoardConfig.py` | 工具接受 `--resume` 参数，不得报 `unrecognized arguments: --resume`。 |
| TH-SAVE-18 | 人物口播运行面板中 `04_01` 完成且有 passthrough warning | 步骤行只显示 `SRT 改写 / 完成 / 耗时`，不得显示 `talking_head_script_passthrough...` 小字。 |
| TH-SAVE-19 | 人物口播打开 `重新运行` 弹窗 | 每个步骤左侧显示连续顺序标签 `1` 到 `5`，对应 `运行变量准备 / 口播脚本改写 / 故事版生成 / 故事版分镜生成 / 故事版配置`。 |
| TH-SAVE-20 | 人物口播打开 `一键成片` 面板 | 每个步骤左侧显示连续顺序标签 `1` 到 `8`。 |
| TH-SAVE-21 | 人物口播完成 StoryBoard 生成后打开 StoryBoard 页面 | 页面左侧只能出现 `shot_001` 和 `scene_001`；不得出现 `shot_001...shot_016` 或多个 scene。 |
| TH-SAVE-22 | StoryBoard 模型或提示词返回多 Shot / 多 Scene | `TalkingHead_V1/02` 必须强制规整为 `1 Shot + 1 Scene + 按单个视频长度合并后的 Dialogue/Segment`，且页面读取产物也同步更新。 |
| TH-SAVE-23 | 首帧覆盖视频个数为 `2`，共有 5 个 Segment | 第 `1/3/5` 个 Segment 的 `Image_New` 必须放人物形象图，第 `2/4` 个 Segment 的 `Image_New.path/source_type/source_path` 必须为空，只在 `video_plan` 记录 `previous_segment_tail_frame`。 |
| TH-SAVE-34 | 第 `2/4/6...` 个 Segment 尚未执行 `05_02` | StoryBoard 页面不得出现坏图或不存在的 `TailFrame.png` 链接；尾帧覆盖只能由 `05_02` 后续执行。 |
| TH-SAVE-24 | 人物口播选择 HeyGen 克隆声音和 Tempo 后运行 StoryBoard 配置 | 每个 Dialogue 都必须生成或绑定实际音频，`Audio_Final` 不得为空槽位。 |
| TH-SAVE-25 | HeyGen 校准与最终音频生成完成 | `02` 必须有真实校准音频和 `seconds_per_unit`；`03` 必须按合并后的每个 Dialogue 生成最终音频，并把 Dialogue duration 回写为最终 wav 的实际时长，而不是固定 8 秒、12 秒或纯字数估算。 |
| TH-SAVE-28 | 单个视频长度为 `12`，16 条短 SRT 运行 StoryBoard | 不能生成 16 个 Dialogue；应按校准时长把连续 SRT 合并成若干个不超过 12 秒的 Dialogue/Segment，且每个 Dialogue 保留 `srt_ids`。 |
| TH-SAVE-29 | 人物口播已选择 HeyGen 声音和 Tempo，运行 `00` | `Variables.json.talking_head.voice_timing` 和 `Variables.json.default_voice_clone_config` 同时存在，包含选中 voice、Tempo、provider/model、`api_key_ref`，但不包含 API Key 明文。 |
| TH-SAVE-30 | 运行 `02/03` 生成或复用 HeyGen 音频 | 音频 manifest 记录 `voice_id / tempo / api_key_ref / runtime_config_source`，证明任务级声音选择来自 Session Variables。 |
| TH-SAVE-31 | 人物口播 Task 在 StoryBoard 页点击 Session Variables 刷新 | 后端运行 `TalkingHead_V1/00_PrepareSessionVariables.py`，返回的 `Variables.json.workflow_profile.profile_id=person_talking_head_v1`，`storyboard_quick_config.segment_policy=merge_srt_to_single_video_length`。 |
| TH-SAVE-32 | 动作模拟 Task 在 StoryBoard 页点击 Session Variables 刷新 | 后端运行 `DanceMimic_V1/00_PrepareSessionVariables.py`，不得只读取旧变量，也不得运行 `Analysis_V1/00`。 |
| TH-SAVE-33 | 普通 Analysis / 脚本生成 Task 在 StoryBoard 页点击 Session Variables 刷新 | 后端运行 `Analysis_V1/00_PrepareSessionVariables.py`，不注入人物口播或动作模拟专属字段。 |
| TH-SAVE-35 | 人物口播 03 已生成 5 段 Dialogue 音频，但 05_02 只刚开始执行 | 一键成片逐句状态中 5 段 `音频` 都应为绿色 `音频已匹配`；不得因 `SegmentAudio_Final` 尚未全部生成而显示白色。 |
| TH-SAVE-36 | 人物口播运行摘要只包含 `lipsync.sync_mode=lipsync`、`reason=dialogue_marked_talking_head`，未包含 `dialogue_audio_tasks` | 前端 adapter 仍应把音频显示为已匹配；若要更严格，后端状态汇总必须补充 per-dialogue audio 文件状态，而不是让 UI 等待不存在的摘要字段。 |
| TH-SAVE-37 | 人物口播已选 HeyGen 声音和 Tempo，打开 StoryBoard Timing 面板 | Provider / Model / Voice / Tempo 显示当前人物口播 selection；不得显示旧 Qwen/Cherry。 |
| TH-SAVE-38 | 在 StoryBoard Timing 面板推荐声音后修改 Tempo 为 `1.4` 并保存、刷新 | Tempo 仍为 `1.4`，voice 仍为同一个 HeyGen 克隆声音，`storyboard_tts_selection.top_candidates` 不为空。 |
| TH-SAVE-39 | 在 StoryBoard Timing 面板修改 Tempo 并点击保存，不刷新页面 | 当前面板值立即保持新 Tempo；落盘检查 `koubo_storyboard_edit.json`、`Variables.json`、`task_meta.json` 三处均为新 Tempo。 |
| TH-SAVE-40 | 在 StoryBoard 中点击某段 `生成TTS` | SSE 返回 `completed` 和 `round_completed`，后台日志无 `storyboard_source_changed` / 409，`tts_manifests/{dialogue_key}_Audio_Final.json` 写入 provider/model/voice/tempo/duration/output。 |
| TH-SAVE-41 | TTS 生成完成后刷新 StoryBoard | Dialogue 显示时长、`koubo_storyboard_edit.json`、`srt_storyboard.json`、manifest `duration_seconds` 一致；Scene/Shot 总时长等于所有 Dialogue 真实音频时长之和。 |
| TH-SAVE-42 | 已有 TTS manifest，页面只读取缓存不重新生成 | 后端加载 StoryBoard 返回的 plan 仍使用 manifest duration，不回退到旧的字数估算时长。 |
| TH-SAVE-43 | 人物口播 Task 在 StoryBoard 的 Video Plan 弹窗点击执行 | 后端固定运行 `Analysis_V1/05_02_VideoPlanExecutor.py`；不得因 `workflow_id=person_talking_head_v1` 切到 TalkingHead 05_02。 |
| TH-SAVE-44 | 人物口播 Task 在 StoryBoard 的 Video Prompt 编辑器点击“重新加载” | 只使用 `TalkingHead_V1/05_02 + Video_SDR2V_TalkingHead.md` 重建 Prompt；不得启动 TalkingHead 05_02 全执行链。 |
| TH-SAVE-45 | 人物口播 Task 点击“一键成片”并执行到 05_02 | 此入口运行 `TalkingHead_V1/05_02_VideoPlanExecutor.py`，与 StoryBoard Video Plan 的 Analysis 05_02 严格区分。 |
| TH-SAVE-26 | 人物口播运行日志 / plan steps 检查 | 不得出现 `Analysis_V1/04_03_StoryBoardQuick.py` 作为执行步骤；只允许 `TalkingHead_V1/01` 保留其最终提示词生成结构能力。 |
| TH-SAVE-27 | 检查 git diff 或代码审查 | `ToolLibrary/Analysis_V1/05_01_VideoPlanGenerator.py` 不得包含人物口播专属新增逻辑；人物口播差异必须在 `TalkingHead_V1` 内落地。 |
