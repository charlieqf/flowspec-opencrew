# DanceMimic_V1 实施收敛设计

版本：v0.6

状态：**文档层已推进到 implementation-ready**（已做四轮代码/文档核验）。本文已把 DanceMimic_V1 的入口、runner、Task、03 StoryBoard 合同、05_xx OpenRouter 路由收敛为可实现方案；开工仍需按 §13 做遗留清账、migration、实现与 smoke，但这些是实施验收项，不再是待设计问题。原则是：入口和 runner 独立，ToolLibrary/DanceMimic_V1 保持薄封装，大部分通用能力复用或扩充 ToolLibrary/Analysis_V1。

v0.3 变更：修正第二轮代码核验中 6 处与现状不符/写窄之处——runner 并非可直接复用、05_xx 需签名级改造、故事板口播文案/metadata 改造为必做项、`workflow_mode` 列实际不在 `openclip_tasks`、上游合同需正文化、DanceMimic_V1 目录已有遗留文件。新增 §14 上游数据契约细则。

第三轮再修正 4 处：(1) MaxSR2→openrouter 非默认假设，05_02 实际路由到 `video_seedance`，改为 blocking 实现项（§8.3-3、D2）；(2) §8.2 标题与 §11 B1/B2 去掉「唯一缺口/仅补 context」旧口径，明确为多处改造；(3) §7 状态分两层（step lifecycle 7 态 / terminal result 3 态 + warnings）消除与 M2 冲突；(4) 文档状态标注为「设计收敛但未 implementation-ready」，03 独立实现文档仍缺。所有 `file:line` 按当前仓库状态。

v0.4 变更：补齐 `DanceMimic_V1_03_StoryBoardStandardTaskBuild_工具实现需求.md`；把 D2 从“移植路由或 plan 写 openrouter”的二选一收敛为唯一方案：DanceMimic reference-video 任务强制走 `video_openrouter.py` + `bytedance/seedance-2.0` + `input_references`，05_01/05_05 写 plan 字段，05_02/05_06 执行时路由并校验；§13 改为 implementation checklist。

v0.5 变更：新增 `DanceMimic_V1_回归测试设计.md`，把“现有口播/Analysis_V1 不回归”和“DanceMimic 专属 reference-video 链路命中 OpenRouter”列为合并/发布阻断门。

v0.6 变更：采纳回归测试评审意见，补 02 遮脸有效性 P0 自动 QA、分段正向边界、H2 manifest 缺失 blocked、M1 force 级联 stale、CI fake detector 策略、发布前人工终审；同时澄清 D2：plan 写 OpenRouter 是主路径，seedance/MaxSR2 归一 helper 仅作兜底。

### 0. 代码核验关键结论（决定改造范围）

1. **入口按钮已存在但为 stub**：`frontend/src/modules/koubo/KouboTaskList/KouboTaskCreateMenu.jsx:5` 已有「DanceMimic」按钮；handler `KouboTaskListPage.jsx:71-74` 当前只 `setError("…后端 dance_mimic_v1 创建接口和运行页尚未接入")`。即前端骨架已起，需接真实创建/运行。
2. **Runner 只能复用「机制」，不能直接复用「命令编排」**（v0.3 修正）：`subprocess.Popen` 执行 + 状态轮询 + 日志是通用机制可复用；但 `analysis_v1_step_command()`（`router.py:2928`）形参绑定 `OpenClipAnalysisV1RunPayload`，并对 `00/02_01/03_0X/04_01/04_02` 等 Analysis 步骤硬编码参数拼接；attempt/surface 也绑死 `ANALYSIS_V1_ATTEMPT_FAMILY = "analysis_v1_tool_run"`（`router.py:332`）/ `SURFACE_ANALYSIS_V1_RUN`。**必须新增 `dance_mimic_step_command()` + 独立 attempt family/surface，或先抽 generic tool runner**，不能按字面「直接复用」。见 §4.4。
3. **执行链改造是签名级**（v0.3 修正）：provider 侧 `video_openrouter.py` 已消费 `context["reference_images"|"reference_audios"|"reference_videos"]`（`:787-797`）并组装 `input_references`（`:638-645`）；但 `05_02.generate_video_with_provider()`（`:1114`）签名里**没有 segment / reference_videos 形参**，WAN/Kling 靠 config 探测 + 固定文件名硬塞，调用点（`05_02:2499`、`05_06:624`）只传 `[first_frame]`。DanceMimic 每段参考视频不同，必须改函数签名并在两个调用点解析/复制/传参。见 §8。
4. **dak 显式优先可用**：`asset_core_services.py:136/:161` 的 `derive_dialogue_asset_key()` 优先采用 dialogue 上已写明的 `dialogue_asset_key`，无才回退 `new_dialogue_asset_key()` 的 `dak_{uuid4().hex[:12]}`（`:129-133`）。故 03 写显式 `dak_NNNN` 不会被 UUID 化。见 §6。
5. **`workflow_mode` 列不在 `openclip_tasks`**（v0.3 修正）：`schema.py` 的 `openclip_tasks`（`:383-417`）无 `workflow_mode`；该列目前只在口播复刻 `oc_rebuild_tasks`（`:513`）上。`openclip_attempts`（`:460`）也无 attempt family 列。故需新增 `openclip_tasks.workflow_mode` migration/ensure + 历史回填 + 各读取点透传。见 §2。
6. **DanceMimic_V1 目录已有遗留文件且其一是错工具**（v0.3 修正）：当前 `ToolLibrary/DanceMimic_V1/` 已存在 `00_PrepareSessionVariables.py`（41KB，疑似 Analysis_V1 拷贝）与 `01_VideoProbeMetadata.py`（口播视频探测，**不是** `01_ReferenceMediaDemux` 音画拆分）。必须明确去留，否则实现会沿用「视频探测」而非「参考视频音画拆分」。见 §5。

## 1. 结论摘要

推荐方案：

1. `任务列表（口播）` 顶部新增 `DanceMimic` 创建入口。
2. DanceMimic 使用独立页面与路由：`#/dance-mimic/tasks/{task_id}`。
3. DanceMimic 使用独立 backend surface / runner target：`dance_mimic_v1`。
4. 不复用 Analysis_V1 的 `analysis-v1/run-to-storyboard` 7 步任务链。
5. 复用 `openclip_tasks` 作为 StoryBoard 兼容 task 记录，但需要可区分 workflow。
6. `ToolLibrary/DanceMimic_V1/` 只放少量专属文件和 wrapper；通用逻辑放回 `ToolLibrary/Analysis_V1` 或 shared service。
7. 03 生成标准 StoryBoard 后，用户进入现有 StoryBoard 页面继续执行 Video Plan / Image Plan / Video Only Plan。

### 1.1 与当前口播视频复刻的边界

DanceMimic 是“舞蹈/动作视频复刻”，不是当前 OC-Rebuild 的“口播视频复刻”。

必须明确分开：

1. **不使用 `oc_rebuild_tasks` 作为主 task 表**，不复用 `#/ocrebuild/tasks/{id}` 页面作为 DanceMimic 主页面。
2. **不复用 `ToolLibrary/Rebuild_V1` 的口播复刻工具链**，例如 source package、`rebuild_shot_plan.json`、SRT rewrite、TTS voice builder、host product builder、口播人物/产品替换策略等。
3. **不向用户暴露口播复刻字段**，例如目标平台、目标受众、产品信息、字幕风格、标题样式、voice style、TTS 声音选择等，除非后续明确要做“带口播的舞蹈短视频”扩展。
4. **只复用通用基础设施经验**，例如 task/attempt 状态、文件审计、流式运行反馈、资产对比/确认交互、provider 配置读取、参考素材校验；不能复用口播复刻的产品心智和步骤命名。
5. DanceMimic 的核心用户任务是：上传一段参考舞蹈/动作视频，指定或补充目标主体/场景/风格，系统把动作分段并生成可被 MaxSR2 reference-video 能力消费的每段动作参考。

因此，`DanceMimic` 按钮可以短期出现在 `任务列表（口播）` 顶部用于产品试验，但页面标题、弹窗标题和任务类型标签应显示为“舞蹈复刻 / DanceMimic”，不能让用户以为这是“口播视频复刻”的一个子模式。中长期更合理的是把它提升为独立视频工作流入口，或纳入一个中性的“视频任务”列表，而不是长期挂在“口播”命名下。

### 1.2 与当前故事板页面的复用边界

用户希望复用当前“故事板”页面，这个目标合理，并且应作为 DanceMimic 的核心集成方式。

推荐边界：

1. **前置入口不复用**：DanceMimic 创建、参数配置、00-03 运行进度使用独立页面和 `dance_mimic_v1` runner。
2. **中间合同复用**：03 必须把 DanceMimic 结果转换成当前 `#/koubo-storyboard/tasks/{task_id}` 能读取的标准 StoryBoard 文件结构。
3. **后置页面复用**：03 完成后，用户点击 `打开故事板` 进入当前 StoryBoard 页面，在那里继续编辑 Dialogue、查看素材、调整图片/视频、执行 Video Plan / Image Plan / Video Only Plan。
4. **任务 id 复用**：DanceMimic task 复用同一个 `openclip_tasks.id` 打开 StoryBoard，不再复制成 `oc_rebuild_tasks` 或另一个 StoryBoard task，避免用户看到两个任务。
5. **页面能力复用，文案需适配**：当前 StoryBoard 页面可以复用，但应根据 `workflow_mode=dance_mimic_v1` 调整标题、空态和部分提示词文案，例如从“故事版（口播）”改为“故事板（舞蹈复刻）”或通用“故事板”。

因此最终用户路径应是：

```text
任务列表顶部 DanceMimic
-> DanceMimic 创建弹窗
-> #/dance-mimic/tasks/{task_id} 跑 00-03
-> #/koubo-storyboard/tasks/{task_id} 复用当前故事板页面
-> 在故事板里继续生成/编辑/执行视频
```

这不是“复用口播视频复刻”，而是“把舞蹈复刻预处理结果适配成当前 StoryBoard 可编辑任务”。

## 2. Task 模型

### 2.1 推荐正式方案

复用 `openclip_tasks` 表，但给它增加 workflow discriminator：

```text
openclip_tasks.workflow_mode TEXT
```

取值：

```text
analysis_v1
script
dance_mimic_v1
```

**现状核验（v0.3）**：`schema.py` 的 `openclip_tasks`（`:383-417`）**当前没有** `workflow_mode` 列；该列只存在于口播复刻 `oc_rebuild_tasks`（`:513`），可作为加列的写法先例。`openclip_attempts`（`:460`）也无 attempt family 列。因此本方案需要完整的「加列 + 回填 + 透传」四件套，缺一会导致列表/路由/StoryBoard 无法区分来源：

1. **migration / ensure column**：为 `openclip_tasks` 增加 `workflow_mode TEXT`（参照 `oc_rebuild_tasks:513` 的列定义与 ensure-column 逻辑）。
2. **写入**：创建接口（§4.1）写 `workflow_mode = dance_mimic_v1`。
3. **历史回填**：有 `task_meta.json.create_mode = script` 的设 `script`；其它历史 `openclip_tasks` 设 `analysis_v1`。
4. **读取并透传**：task list serialization、route guard、storyboard list/detail（见 §3.5 故事板适配）都要读 `workflow_mode` 并透传给前端，用于入口主操作与文案分支。

说明：

1. 现有 `openclip_tasks` 已经是 StoryBoard 入口的兼容 task 表。
2. 每个 `session_id` 唯一的约束可以保留；DanceMimic 也是一条 session 对应一条 task。
3. 新增 `workflow_mode` 后，任务列表、路由、删除、审计、后续 StoryBoard 绑定都能稳定区分来源。

配套字段仍写入：

```json
{
  "schema_version": "koubo_task_list_meta_0.2",
  "workflow_id": "dance_mimic_v1",
  "create_mode": "dance_mimic",
  "input_mode": "reference_video",
  "dance_mimic": {
    "reference_video_path": "SessionContext/Video_Reference_Source.mp4",
    "segment_target_seconds": 8,
    "segment_min_seconds": 4,
    "default_video_model_alias": "MaxSR2"
  }
}
```

### 2.2 MVP 退路

如果暂时不做 DB migration，可以先只依赖：

```text
SessionOutput/task_list/task_meta.json.workflow_id = dance_mimic_v1
SessionOutput/task_list/task_meta.json.create_mode = dance_mimic
```

但这是过渡方案。缺点是 DB 查询、统计、删除审计和 route guard 都要读 workspace 文件，长期不如 `openclip_tasks.workflow_mode` 稳定。

## 3. 创建入口与 UI

### 3.1 任务列表顶部入口

`任务列表（口播）` 顶部按钮组：

```text
视频分析 | DanceMimic | 脚本生成
```

点击 `DanceMimic` 后打开 DanceMimic 创建弹窗，不直接创建 Analysis_V1 task。

### 3.2 DanceMimic 创建弹窗

MVP 字段：

```text
参考视频：必填，支持上传 mp4/mov/m4v
任务标题：可选，默认取文件名
目标分段秒数：默认 8
最小分段秒数：默认 4
默认视频模型：MaxSR2，只显示，不暴露 key
创建后动作：进入 DanceMimic 任务页
```

不建议在创建弹窗里放 Prompt Builder、SRT 改写、TTS 等口播分析字段。

### 3.3 DanceMimic 任务页

路由：

```text
#/dance-mimic/tasks/{task_id}
```

页面只显示 DanceMimic 自己的逻辑步骤：

```text
00 准备会话变量
01 参考视频音画拆分
02 参考视频人脸遮挡构建
03 标准 StoryBoard 构建
```

运行完成后显示 `打开故事板`，跳转：

```text
#/koubo-storyboard/tasks/{task_id}
```

### 3.4 任务列表行内操作

任务列表每行根据 `workflow_mode/create_mode` 决定主操作：

```text
analysis_v1/script -> 打开视频分析
dance_mimic_v1 -> 打开 DanceMimic
有 storyboard -> 打开故事板
```

不要对 DanceMimic task 默认显示 “打开视频分析”，避免把用户带入 Analysis_V1 7 步页面。

### 3.5 故事板口播文案 / metadata 适配（v0.3 新增，必做项）

复用故事板页面不只是「打开同一个页面」，现有故事板列表与空态把口播心智**硬编码**了，DanceMimic task 进入后会显示错误文案：

- 后端 `koubo_storyboard/task_routes.py:42-43` 硬编码 `"title": "故事版（口播）"`、`"source_type": "analysis_v1_storyboard"`。
- 前端 `KouboStoryBoard/components/KouboTaskList.jsx:5` 空态硬编码「还没有可用的故事版（口播）。请先在视频分析（口播）中生成…」。

**必做改造**：

1. 后端 storyboard list/detail 读取 task 的 `workflow_mode`（§2），对 `dance_mimic_v1` 返回 `title="故事板（舞蹈复刻）"`、`source_type="dance_mimic_v1_storyboard"`（或中性「故事板」）。
2. 前端列表卡片、空态、提示文案按 `workflow_mode/source_type` 分支；DanceMimic 空态指向「DanceMimic 运行 00–03」而非「视频分析（口播）」。
3. 弱化/隐藏对 DanceMimic 无意义的口播专属提示（TTS、字幕改写、voice 选择等）。
4. 该项纳入 §9 实施顺序，作为「复用故事板页面」的组成部分，不能只做数据不做文案。

## 4. Backend API

### 4.1 创建接口

推荐新增：

```text
POST /api/dance-mimic/tasks
```

请求可使用 `multipart/form-data`，包含：

```text
reference_video: file
title: string
segment_target_seconds: number
segment_min_seconds: number
run_after_create: boolean
```

返回：

```json
{
  "ok": true,
  "task_id": 123,
  "session_id": 456,
  "workspace_dir": "...",
  "task_url": "#/dance-mimic/tasks/123",
  "storyboard_url": "#/koubo-storyboard/tasks/123"
}
```

创建时写入：

```text
openclip_tasks.workflow_mode = dance_mimic_v1
openclip_tasks.reference_video_path = <workspace-relative or absolute input path policy>
SessionContext/Video_Reference_Source.mp4
SessionOutput/task_list/task_meta.json
```

### 4.2 任务详情

```text
GET /api/dance-mimic/tasks/{task_id}
```

返回 task、session、workspace、当前产物摘要、最新 attempt 状态。

### 4.3 运行计划

```text
GET /api/dance-mimic/tasks/{task_id}/run/plan
```

返回：

```json
{
  "target": "dance_mimic_v1",
  "default_mode": "run_all",
  "steps": [
    {"id": "00", "display_name_zh": "准备会话变量", "status": "pending"},
    {"id": "01", "display_name_zh": "参考视频音画拆分", "status": "pending"},
    {"id": "02", "display_name_zh": "参考视频人脸遮挡构建", "status": "pending"},
    {"id": "03", "display_name_zh": "标准 StoryBoard 构建", "status": "pending"}
  ]
}
```

### 4.4 运行与状态

```text
POST /api/dance-mimic/tasks/{task_id}/run
GET  /api/dance-mimic/tasks/{task_id}/run/{attempt_id}
POST /api/dance-mimic/tasks/{task_id}/run/{attempt_id}/stop
GET  /api/dance-mimic/tasks/{task_id}/run/{attempt_id}/steps/{step_id}/logs
```

可复用 Analysis_V1 runner 的状态模型和轮询 UI，但 `target`、step specs、命令编排必须是 `dance_mimic_v1`。

**runner 复用边界（v0.3 修正，对应 §0.2）**：现有 `analysis_v1_step_command()`（`router.py:2928`）形参绑定 `OpenClipAnalysisV1RunPayload` 且按 Analysis 步骤 id 硬编码参数；attempt/surface 绑死 `ANALYSIS_V1_ATTEMPT_FAMILY`（`router.py:332`）/ `SURFACE_ANALYSIS_V1_RUN`。因此**不能**直接复用，须二选一：

1. **抽 generic tool runner**（推荐）：把 `subprocess.Popen + 状态轮询 + 日志 + attempt 记录` 抽成与 workflow 无关的核心，`step_command` 与 `attempt_family` / `surface` 作为参数注入；Analysis_V1 与 DanceMimic 各传自己的实现。
2. **新增 DanceMimic 专用编排**：`dance_mimic_step_command()` + `DANCE_MIMIC_ATTEMPT_FAMILY = "dance_mimic_v1_tool_run"` + 独立 surface 常量；复用 Popen/轮询机制但命令与状态命名独立。

无论哪种，DanceMimic 的 step_command 只需为 `00`（带 `--workflow-id dance_mimic_v1` 等）、`01/02/03`（`--workspace --print-json` + force/resume）拼参，远比 Analysis_V1 简单。

## 5. ToolLibrary 薄封装复用矩阵

| DanceMimic 逻辑步骤 | 推荐实现位置 | DanceMimic_V1 目录是否放脚本 | 说明 |
| --- | --- | --- | --- |
| 00 准备会话变量 | 扩充 `ToolLibrary/Analysis_V1/00_PrepareSessionVariables.py` 或抽 shared helper | 尽量不放，最多薄 wrapper | 增加 `--workflow-id dance_mimic_v1`、分段参数、MaxSR2 alias、参考视频路径字段。 |
| 01 参考视频音画拆分 | 复用 `ToolLibrary/Analysis/media_binaries.py` 与现有 demucs/source separation 能力 | 可放一个薄 wrapper | DanceMimic 输出合同不同，wrapper 负责路径、manifest、audio policy。 |
| 02 人脸遮挡构建 | DanceMimic 专属 | 放专属脚本 | 这是 DanceMimic 核心差异：分段、face track、遮脸参考视频。 |
| 03 标准 StoryBoard 构建 | 优先做 shared service 或扩充 Analysis_V1 StoryBoard 相关服务 | 可放薄 wrapper | 03 不应 LLM 分组；它从 segment manifest 直接生成固定 1 Shot / 1 Scene / N Dialogue。 |
| 05_01/05_05 plan 生成 | 扩充 `ToolLibrary/Analysis_V1` | 不放 | 读取 DanceMimic seed/metadata，为每段写 `reference_video_path` 等字段。 |
| 05_02/05_06 执行 | 扩充 `ToolLibrary/Analysis_V1` | 不放 | 支持 per-segment reference video 传入 OpenRouter MaxSR2。 |

`ToolLibrary/DanceMimic_V1/` 建议最小结构：

```text
ToolLibrary/DanceMimic_V1/
  tool_registry.json
  README.md
  Reference/
    01_ReferenceMediaDemux.md
    02_ReferenceFaceMaskedVideoBuild.md
    03_StoryBoardStandardTaskBuild.md
  01_ReferenceMediaDemux.py              # 可选薄 wrapper
  02_ReferenceFaceMaskedVideoBuild.py    # 专属核心脚本
  03_StoryBoardStandardTaskBuild.py      # 可选薄 wrapper
```

如果 00、03 都能完全由 backend/shared service 编排，则 DanceMimic_V1 目录可以不放对应脚本，只在 `tool_registry.json` 中声明逻辑步骤和实际 implementation ref。

### 5.1 现有遗留文件处置（v0.3 新增，必须先清账）

当前 `ToolLibrary/DanceMimic_V1/` 已存在两个与本设计不一致的遗留文件，开工前必须明确去留，否则实现会误用「视频探测」而非「参考视频音画拆分」：

| 现有文件 | 性质 | 处置决策 |
| --- | --- | --- |
| `00_PrepareSessionVariables.py`（41KB，疑似 Analysis_V1 拷贝） | 与「复用 Analysis_V1/00 + workflow 分支」原则冲突 | **废弃拷贝**，改为复用 `Analysis_V1/00`（加 `--workflow-id dance_mimic_v1` 分支）；若坚持目录自洽，仅保留**极薄 wrapper**（转调 + 传参），不保留 41KB 全量副本。 |
| `01_VideoProbeMetadata.py`（口播视频元数据探测） | **错的工具**：DanceMimic 的 01 是 `01_ReferenceMediaDemux`（去音轨/混合音/纯人声），不是元数据探测 | **删除/重命名废弃**，新建 `01_ReferenceMediaDemux.py`。视频元数据探测若 02 需要，可在 02 内用 ffprobe 解决，不作为独立 01 步骤。 |

清账动作应在 §9 实施顺序第 1 步前完成，并在 `tool_registry.json` 初版中只声明 DanceMimic 真实步骤（00/01/02/03 的 DanceMimic 语义），不残留 `01_VideoProbeMetadata`。

## 6. 03 StoryBoard 合同

完整实现合同见 `DanceMimic_V1_03_StoryBoardStandardTaskBuild_工具实现需求.md`。本节只保留主链路必须遵守的摘要。

03 必须明确产出：

```text
SessionOutput/storyboard/srt_storyboard.json
SessionOutput/storyboard/assets/videos/{dialogue_asset_key}_Reference_FaceMasked.mp4
SessionOutput/storyboard/storyboard_seed.json
S4_03_StoryBoardStandardTaskBuild/Report/Result.json
```

推荐 03 直接写显式稳定 key：

```json
{
  "dialogue_asset_key": "dak_0001",
  "dance_mimic": {
    "reference_video_path": "SessionOutput/storyboard/assets/videos/dak_0001_Reference_FaceMasked.mp4",
    "reference_video_role": "dance_mimic_segment_motion_reference"
  }
}
```

要求：

1. 每个 Dialogue 的 `dialogue_asset_key` 必须稳定，不让后端懒生成 UUID。
2. `dak_NNNN` 与 `reference_segments_manifest.json` 一一对应。
3. 如果选择不预生成 `koubo_storyboard_edit.json`，也必须验证懒生成后 key 不变。
4. `reference_video_path` 指向 StoryBoard 资产副本，不指向 02 的工作目录源文件。
5. `working_assets.video.path` 初始为空，不能把 face-masked reference video 当成 `Video_Final`。
6. 03 成功后更新 task meta：
   - `storyboard_status = generated`
   - `dance_mimic.completed_steps` 包含 00-03
   - `storyboard_url = #/koubo-storyboard/tasks/{task_id}`

## 7. 状态与 rerun

**状态分两层（v0.3 澄清，消除与 §11 M2 的口径冲突）**：

1. **step lifecycle 状态**（API/UI 展示单步进度，7 态）：

   ```text
   pending  running  completed  blocked  failed  skipped  stale
   ```

2. **terminal result 状态**（工具 `Result.json` 落盘、对外汇总，仅 3 态）+ 附属字段：

   ```text
   completed | blocked | failed     (+ warnings[]：承载 completed_with_warnings / partial / sync_pending 等细分语义)
   ```

即：`pending/running` 是运行期生命周期，`skipped/stale` 是编排层派生态（跳过/上游失效），它们**不进** `Result.json` 的 terminal status；`Result.json.status` 永远是三态之一，细分用 `warnings[]`。runner/UI 按 step lifecycle 展示，落盘与汇总按 terminal result。

建议：

1. `blocked` 用于缺输入、无可行分段、缺模型配置、缺依赖。
2. `failed` 用于脚本异常、ffmpeg 失败、检测器崩溃。
3. `completed_with_warnings` 不作为顶层状态，改为 `completed + warnings[]`。
4. 01 force 会使 02/03 stale。
5. 02 force 会使 03、StoryBoard assets、Video Plan stale。
6. 03 force 需要先归档旧 `SessionOutput/storyboard/Working` 和相关 assets，再写新版本。

## 8. 后续执行链（B1 + B2 + M3，范围已核验收窄）

DanceMimic 进入 StoryBoard 后，不走 DanceMimic runner 生成最终视频，而走现有 StoryBoard 05_xx 链。

### 8.1 provider 侧已就绪（不改）

`video_openrouter.py` 已支持多模态参考，无需改动：

```python
# :787-797 generate() 读取 context
reference_images = [...context.get("reference_images")...]
reference_audios = [...context.get("reference_audios")...]
reference_videos = [...context.get("reference_videos")...]
# :638-645 组装 input_references（image/audio/video）→ DEFAULT_INPUT_REFERENCE_LIMIT
```

### 8.2 05_xx 执行链缺口（多处，非单点）

provider 模块虽已就绪，但执行链有 4 处缺口：路由（§8.3-3）、plan 字段（§8.3-1，05_01/05_05）、函数签名 + 两个调用点传参（§8.3-2，05_02/05_06）。其中最直观的一处是 `05_02.generate_video_with_provider()`（`:1114-1131`）当前：

```python
context = {"config":..., "reference_images":[首帧], ...}        # :1118 仅首帧
if is_wan_rtv_model(...):  context["reference_videos"] = [固定 Video_Wan_R2V.mp4]    # :1125
if is_kling_omni_model(...): context["reference_videos"] = [固定 Video_Kling_Omni.mp4]  # :1127
# 不为 openrouter/SR2 注入 per-segment reference_videos，也从不注入 reference_audios
```

### 8.3 改造清单（收敛）

1. **05_01/05_05 plan 字段（M3）**：识别 DanceMimic metadata（读 `storyboard_seed.json` 或 task `workflow_mode`），segment video task 写入：

   ```json
   {
     "video_generation_mode": "dance_mimic_sdr2v",
     "provider": "openrouter",
     "model": "bytedance/seedance-2.0",
     "model_alias": "MaxSR2",
     "reference_mode": "input_references",
     "prompt_template": "Video_SDR2V_DanceMimic.md",
     "reference_video_path": "SessionOutput/storyboard/assets/videos/dak_0001_Reference_FaceMasked.mp4",
     "reference_video_role": "dance_mimic_segment_motion_reference"
   }
   ```

   现状证据（M3）：`05_01_VideoPlanGenerator.py` 中无任何 `storyboard_seed` / `reference_video_path` / `video_generation_mode` 字样，须新增。
2. **05_02/05_06 签名级改造（B1/B2，v0.3 修正）**：当前 `generate_video_with_provider(config, prompt_path, output_path, reference_images, duration, timeout_seconds, provider_task_state_path=None, audio_duration=None)`（`05_02:1114`）**没有 segment / reference_videos 形参**，WAN/Kling 是靠函数内 `is_wan_rtv_model(config)` 探测 + 固定文件名硬塞，无法表达「每段不同」。改造：

   - 函数签名新增 `reference_videos: list[Path] | None = None, reference_audios: list[Path] | None = None`，并在 `reference_videos` 非空时 `context["reference_videos"] = [...]`（与 WAN/Kling 固定路径分支互斥）。
   - **两个调用点都要改**：`05_02:2499`（`generate_video_with_provider(video_config, …, [first_frame_path], …)`）与 `05_06:624`（`VPE.generate_video_with_provider(video_config, …, [first_frame], …)`）—— 从当前 segment/plan 解析 `reference_video_path`，复制到本工具 `Working/`，记录到 model_call 审计，并作为 `reference_videos=[...]` 传入。
   - 仅 DanceMimic/SR2 task 生效，不影响普通 openrouter 任务（无 `reference_video_path` 时行为不变）。
3. **路由改造（B1/B2，v0.4 收敛为唯一方案）**：当前 05_02 路径会把 `MaxSR2` 路由到火山 `video_seedance`（first-frame-only），**不会**走 `video_openrouter`，DanceMimic 必须显式强制到 OpenRouter：

   - **现状证据**：`MaxSR2` alias 在 `models.json:13` 归属 `provider: seedance`（model `bytedance/seedance-2.0`）；`provider_selection`（`05_02:582`）据此解析出 `provider=seedance`；`video_module_for`（`05_02:649`）对 `provider in {seedance,…} or "seedance" in model` 返回 `video_seedance`。`video_openrouter` 仅在 `provider in {openrouter,…}` 时命中（`:647`，先于 `:649`）。
   - **关键差异**：把 seedance 重路由到 openrouter 的 `route_seedance_video_provider()` 在 **Asset Library**（`asset_video_generation_services.py:170`），**05_02/05_06 并不调用它**。所以「默认走 OpenRouter」在 Analysis_V1 执行链里**当前不成立**。
   - **实施决策**：不新增 `video_sdr2v_dancemimic.py` provider 模块；复用 `video_openrouter.py` + `Video_SDR2V_DanceMimic.md`。plan 写 `provider=openrouter`、`model=bytedance/seedance-2.0` 是主路径；把 `route_seedance_video_provider()` 的等价逻辑下沉/复制到 05_02 可复用 helper（建议名 `route_seedance_to_openrouter_for_input_references(provider, model)`）只是兜底，用于 CLI override、历史 plan 或 Variables default 仍给出 seedance/MaxSR2 的情况，且仅在 DanceMimic reference-video 场景启用。
   - **触发条件**：`workflow_mode=dance_mimic_v1` 或当前 segment/task 带 `reference_video_path` + `reference_mode=input_references`。普通 Analysis_V1 的 seedance 任务不改变现有 `video_seedance` 路由。
   - **选择顺序**：05_02/05_06 解析视频模型时使用 `CLI override -> plan provider/model -> Variables default`；正常 DanceMimic plan 应直接得到 OpenRouter selection。只有初始 selection 仍是 seedance/MaxSR2 且触发 DanceMimic reference-video 条件时，才归一为 `provider=openrouter`、`model=bytedance/seedance-2.0`。
   - **强校验**：`load_provider_config()` 必须用归一后的 provider/model；最终 `video_module_for()` 若不是 `video_openrouter`，直接失败 `dance_mimic_video_provider_mismatch`，不得静默走 `video_seedance`。
   - **blocking 验收**：必须实现并验证「MaxSR2 在 05_02/05_06 路径最终命中 `video_openrouter` 且发出含 video 的 `input_references`」，否则 DanceMimic 拿不到参考视频能力。见 D2。
4. **per-segment 强约束**：每段只用自己的 `reference_video_path`；缺首帧 blocked、缺参考视频 blocked；禁止抽参考视频首帧冒充首帧。
5. **专用模板**：新增 `ToolLibrary/Analysis_V1/Reference/05_02/Video_SDR2V_DanceMimic.md`（OPENCREW 块对齐 `Video_OpenRouter.md`）：图1=身份/构图/背景锚点，视频1=仅动作/姿态/节奏参考，负向含「身份迁移/遮脸网格残留/字幕水印」。

目标是走 OpenRouter MaxSR2 / `input_references`（非火山 `video_seedance.py` 的 first-frame-only 分支），但这是**必须实现/验证的改造**，不是现成默认（见上 §8.3-3 与 D2）。

## 9. 实施顺序

推荐分阶段：

0. **清账遗留文件**（§5.1）：废弃 `DanceMimic_V1/00_PrepareSessionVariables.py` 拷贝、删除 `01_VideoProbeMetadata.py`；写 `tool_registry.json` 初版。
1. 后端 `openclip_tasks.workflow_mode` 加列 + 回填 + 各读取点透传（§2）；或 MVP meta fallback。
2. runner：抽 generic tool runner 或新增 `dance_mimic_step_command()` + 独立 attempt family/surface（§4.4）。
3. 完成 DanceMimic 创建弹窗和 `#/dance-mimic/tasks/{id}` 空页面；接通入口 stub。
4. 实现 `GET run/plan`，先展示 00-03 四步，不跑工具。
5. 实现 00（复用 Analysis_V1/00 + workflow 分支 + Task 注册）/01（音画拆分 + media manifest）。
6. 实现 02 face mask（insightface 默认 + 可行性校验）与 segment manifest。
7. 实现 03 StoryBoard 合同（显式 dak）和 Task 注册/状态更新。
8. **故事板口播文案/metadata 适配**（§3.5）：后端 storyboard list/detail + 前端列表/空态按 `workflow_mode` 分支。
9. 改造 05_01/05_05 plan 字段（M3）。
10. 改造 05_02/05_06 `generate_video_with_provider` 签名 + 两个调用点 per-segment reference video 传参（§8.3）。
11. 端到端 smoke：创建 DanceMimic task -> 跑 00-03 -> 打开故事板（舞蹈复刻文案）-> 生成一段视频。

## 10. 当前按钮临时行为

在后端接口和页面未完成前，`任务列表（口播）` 顶部 `DanceMimic` 按钮只应提示“创建入口已添加，后端未接入”，不得：

1. 调用 `/api/openclip/tasks` 创建 Analysis_V1 task。
2. 跳转 `#/analysis-v1/tasks/{id}`。
3. 进入 Analysis_V1 的 7 步运行弹窗。

这样可以先暴露产品入口位置，同时避免错误的数据和错误的用户心智。

## 11. 评审 findings 映射（《设计评审结果 v1》逐条落地）

| 编号 | 评审结论 | 本文收敛决策 | 落点 |
| --- | --- | --- | --- |
| B1 | 05_02 不读 `provider_module`，且无 per-segment 参考视频通路 | 不靠 `provider_module` 字段；**多处改造**：05_01/05_05 plan 字段、05_02/05_06 函数签名+两个调用点传参、DanceMimic reference-video 场景强制 route 到 openrouter | §8.2–8.3 |
| B2 | OpenRouter MaxSR2 `input_references` 能力已存在，05_xx 未接入每段参考视频 | provider 模块复用 `video_openrouter.py`；新增专用模板；执行链按 §8.3 唯一方案 route、传参并验收 `input_references` | §8.2–8.3、D2 |
| B3 | 源 schema 不写 dak、后端会 UUID 化 | 03 写**显式稳定 `dak_NNNN`**，命中 `derive_dialogue_asset_key` 显式优先分支 | §0.4、§6 |
| B4 | Task 注册 owner 未指定 | 注册 owner = 创建接口/00 写 `openclip_tasks.workflow_mode`；03 finalize 标 `storyboard_status=generated` | §2、§4.1、§6 |
| B5 | 独立入口/Runner + 薄 ToolLibrary | 独立 `dance_mimic_v1` surface/页面/route，薄复用矩阵 | §1–§5 |
| H1 | 输入路径键三套不统一 | **统一为 `source_video_path`**（对齐 `Analysis_V1/00:748`），00 写 01/02 读 | §14.1 |
| H2 | manifest 命名/必需性矛盾 | 工具目录统一 `reference_media_demux_manifest.json`；`SessionOutput/reference/reference_media_manifest.json` 定为 02/03 **必需**入口，缺失 blocked | §14.2 |
| H3 | 缺 03 独立实现文档 | 已补 `DanceMimic_V1_03_StoryBoardStandardTaskBuild_工具实现需求.md`；03 实现按该文档验收 | §6 |
| H4 | 分段缺可行性校验 | 02 blocked 前加 `ceil(D/target) ≤ floor(D/minimum)`，否则 blocked `segment_constraints_infeasible` | §14.3 |
| M1 | force 规则冲突 + 缺级联失效 | 规范开「共享 SessionOutput 工具可清自己声明路径」例外；01 force→02/03 stale，02 force→03/assets/plan stale | §7 |
| M2 | 状态枚举未统一 | 两层：step lifecycle 7 态（含 pending/running/skipped/stale）供 UI；terminal result 仅 `completed/blocked/failed` + `warnings[]` 落盘汇总 | §7 |
| M3 | 05_01/05_05 不读 seed 不产字段 | 扩展 05_01/05_05 消费 seed、写 `reference_video_path` | §8.3-1 |
| M4 | 02 检测引擎不一致 | V1 默认 `insightface_scrfd`，deface 仅 fallback；配置/依赖/schema/验收统一 | §14.4 |
| S1 | 抽取音频去向未定 | 默认 Final 用 `Audio_Reference_Mixed.wav`（05_02 mux 回）；vocal 暂不消费 | §12-D1 |
| S2 | demucs 硬依赖过严 | demucs 缺失 → vocal warning 跳过，不 blocked 主流程 | §12-D1 |
| S3 | edit 文件不应条件生成（原误报） | 不预生成 edit，靠后端懒生成；前提是 03 写稳定 dak | §6 |

## 12. 默认决策与待确认项

| # | 决策点 | 本文默认 | 影响 |
| --- | --- | --- | --- |
| D1 | 最终视频是否保留/使用人声链路（lipsync/重配音） | 否，仅 mux 回 mixed；vocal/demucs 降级可选 | S1/S2、01 依赖 |
| D2 | `MaxSR2` 在 05_02/05_06 如何命中 `video_openrouter` | **已定**：DanceMimic reference-video task 的 plan 写 `provider=openrouter`、`model=bytedance/seedance-2.0`、`reference_mode=input_references` 是主路径；05_02/05_06 的 seedance/MaxSR2→OpenRouter 归一 helper 只作兜底，并校验最终 module | 不修则拿不到参考视频能力（§8.3-3） |
| D3 | `openclip_tasks.workflow_mode` 走 DB migration 还是 MVP meta fallback | 正式走 migration；MVP 可先 meta | 列表/删除/审计稳定性（§2） |
| D4 | 遮脸默认样式（grid_black vs solid_black/mosaic） | grid_black | 隐私强度 vs 观感（M4） |
| D5 | DanceMimic 长期挂「口播」列表还是独立「视频任务」列表 | 短期挂口播列表顶部，中期独立 | 产品信息架构（§1.1） |

## 13. 开工前置 implementation checklist

按 v0.6，设计 gate 已收敛，开工时需要把它们作为 implementation checklist 落地和验收，否则仍会出现「错挂口播 7 步 / 复制第二套工具链 / 无法注册 Task / 无法出视频 / 槽位绑定错 / 口播回归」风险：

1. B5 架构边界（独立 surface/runner + 薄复用矩阵）已定；实现前先清账遗留文件（§5.1）。
2. B1+B2+M3 执行链签名级改造方案已定；实现按 §8.3 唯一方案完成 plan 字段、函数签名、两处调用点、OpenRouter 路由与 smoke。
3. 03 独立实现文档已落地；03 脚本/service 按 `DanceMimic_V1_03_StoryBoardStandardTaskBuild_工具实现需求.md` 实现和验收。
4. 回归测试设计已落地；合并前按 `DanceMimic_V1_回归测试设计.md` 跑 P0，发布前跑 P1 smoke。

## 14. 上游数据契约细则（H1/H2/H4/M4 正文）

§11 映射表的四项契约在此正式化为合同，供 00/01/02 实现直接遵循。

### 14.1 输入路径键唯一真源（H1，v0.3 修正：废弃范围仅限 Variables.json）

废弃范围**只针对 `SessionContext/Variables.json` 这一层的工具级键**，不波及 DB 字段与 task meta：

- **Variables.json 唯一键 = `source_video_path`**，对齐 `Analysis_V1/00_PrepareSessionVariables.py:748`。在 Variables.json 内废弃 `source_reference_video_path` / `reference_video_path` 两个变体；00 写 `Variables.json.source_video_path`（workspace 相对路径，指向 `SessionContext/Video_Reference_Source.mp4`），01/02 读同一键。
- **保留的任务级 / 元数据级字段（不改名、不废弃）**：
  - `openclip_tasks.reference_video_path`（DB 列，`schema.py:389`，与 Analysis_V1 一致）—— §4.1 创建接口仍写它；
  - `task_meta.dance_mimic.reference_video_path`（§2.1 元数据）—— 仍保留。
- **映射关系**：00 负责把 DB `openclip_tasks.reference_video_path`（或 task_meta）解析、复制为 `SessionContext/Video_Reference_Source.mp4`，并在 Variables.json 写 `source_video_path` 指向它。即「任务级 = reference_video_path，工具级 = source_video_path」，00 是两者的唯一映射点。
- `input_files[]` 仅作输入登记清单，不作路径真源；一致性由 00 保证。

### 14.2 Manifest 命名与必需性（H2）

- 工具目录 manifest 统一命名 **`reference_media_demux_manifest.json`**（废弃 `media_demux_manifest.json`）。
- 跨工具入口 **`SessionOutput/reference/reference_media_manifest.json` 定为 02/03 必需**（01 必产、非「可选」）。
- 02 prepare 显式 preflight 校验其存在，缺失即 `blocked`，错误码 `reference_media_manifest_missing`，杜绝「上游成功、下游 blocked」歧义。

### 14.3 分段可行性公式（H4）

02 在生成分段前，blocked 前置校验三连：

```text
1. target_video_seconds >= minimum_video_seconds          否则 blocked: split_config_invalid
2. D >= minimum_video_seconds                             否则 blocked: source_duration_less_than_minimum
3. ceil(D / target) <= floor(D / minimum)                否则 blocked: segment_constraints_infeasible
```

第 3 条是「存在满足 minimum≤len≤target 的 k 段划分」的充要条件（反例 `D=11,target=8,minimum=6` 应 blocked）。切分以 frame index 为真源，manifest 同记秒与帧。

### 14.4 默认人脸检测器（M4）

- V1 默认 **`insightface_scrfd`**（能直接拿 bbox/confidence，可稳定产出强制的 `FaceTrack.json`）。
- `deface` 仅作 fallback/对比基线，不作默认（其 CLI 不保证产出逐帧 FaceTrack）。
- 配置默认值、依赖清单、`FaceTrack.json`/manifest 的 `detection_engine` 示例、验收样例四处必须统一为 `insightface_scrfd`，消除现有文档 deface/insightface 不一致。
- 默认遮盖样式 `grid_black`；`red_grid_guide` 仅作位置引导，不用于隐私遮挡。

## 15. 回归测试门

详细测试矩阵见 `DanceMimic_V1_回归测试设计.md`。实现验收必须同时覆盖：

1. 现有 Analysis_V1 / 口播视频分析入口仍进入 7 步 run-to-storyboard 流程。
2. 普通 StoryBoard 文案、lazy normalize、slot、计划弹窗、`Working/` 写回不受 DanceMimic 影响。
3. 普通 seedance 05_02/05_06 任务不被全局重路由到 OpenRouter。
4. DanceMimic 00-03 产物符合 03 实现合同，参考视频资产不被当成 `Video_Final`。
5. 02 遮脸自动 QA 纳入 P0：expanded bbox 区域满足 `grid_black_black_pixel_ratio_min` / `masked_region_diff_mean_min` 阈值，post-mask re-detect 异常可追踪。
6. DanceMimic 05_01/05_05/05_02/05_06 reference-video 链路最终命中 `video_openrouter.py` 并发送 video `input_references`。
7. 发布前人工抽查 QA sheet、遮脸参考视频和至少一条生成结果，确认脸不漏、原始身份不泄露给云端参考视频、最终动作/节奏没有明显漂移。
