# Koubo 口播任务列表需求设计

版本：v0.2

状态：需求确认稿，用于后续「任务列表（口播）」导航入口、两种创建任务入口、视频分析详情和 StoryBoard 详情统一实现。

## 1. 背景

「故事版（口播）」下面需要的不是单纯 StoryBoard List，而是一个统一的口播任务列表。

这个 Task List 承载两类任务：

1. 视频导入创建：上传或选择参考视频，走视频分析链路，从视频中解析 SRT。
2. 纯脚本创建：不导入视频，由用户输入脚本，系统把脚本转换成标准 SRT JSON。

两类任务都应该创建同样的 Task / Session / workspace，保存同样的 Task 配置字段，并在后续进入同一套 `00`、`04_01`、`04_02` 以及 StoryBoard 明细能力。

因此，左侧导航新增「任务列表（口播）」入口，点击后进入 Task List。Task List 中每一条记录都可以查看两个详情：

1. 视频分析详情：即使没有参考视频，纯脚本任务也应该有一个无参考视频的视频分析详情，用于复用音色选择、音色克隆、脚本重写等能力。
2. StoryBoard 详情：进入现有 StoryBoard 明细页，继续复用分镜、素材池、音频、图片、视频、口型和最终视频能力。

## 2. 核心原则

### 2.1 列表是 Task List，不是 StoryBoard List

列表的主实体是 Task。StoryBoard 是 Task 下游产物和明细视图之一。

页面命名建议：

```text
口播任务列表
```

路由建议：

```text
/#/koubo-storyboard
```

虽然路由可以沿用 `koubo-storyboard`，但页面文案和数据模型应明确为 Task List。

### 2.2 两种创建方式统一为同一种 Task

创建方式不同，但创建后的任务结构必须统一：

```text
创建 Session
创建 workspace
创建 OpenCode session
创建 Task
保存 Task 配置字段
准备标准 SRT JSON
运行 00_PrepareSessionVariables
运行 04_01_SRTRewrite
运行 04_02_StoryBoard
```

差异只在输入来源：

| 创建方式 | 输入 | 标准化产物 | 后续链路 |
| --- | --- | --- | --- |
| 视频分析创建 | 参考视频 | 视频解析得到 `final_srt_frame_items.json` | 统一 |
| 脚本生成创建 | 口播脚本 | 脚本生成 `final_srt_frame_items.json` | 统一 |

统一后的后半段：

```text
final_srt_frame_items.json
  -> 00_PrepareSessionVariables
  -> SessionContext/Variables.json
  -> 04_01_SRTRewrite
  -> rewritten_srt_items.json
  -> 04_02_StoryBoard
  -> srt_storyboard.json
```

### 2.3 Task 配置仍然由配置页保存，再由 00 下发

无论任务来自视频还是脚本，以下配置都不应直接写入 `SessionContext/Variables.json`：

```text
industry
persona
target_audience
product_info
constraints
video_formula
rewrite_simple_prompt
rewrite_final_prompt
storyboard_simple_prompt
storyboard_final_prompt
storyboard_quick_config_json
run_model_provider
run_model_id
```

统一规则：

```text
页面配置
  -> Task DB 字段
  -> 00_PrepareSessionVariables
  -> SessionContext/Variables.json
```

这样视频任务和脚本任务的配置来源一致，重试、恢复、审计、列表展示也都能从 Task 主状态读取。

### 2.4 纯脚本任务也要能进入视频分析详情

纯脚本任务没有参考视频，但仍然应该有视频分析详情页。

这个详情页的语义是「口播分析与脚本工作台」，而不是必须绑定视频。它需要复用：

1. 脚本查看与重写。
2. SRT Rewrite。
3. 音色选择。
4. 音色克隆。
5. 后续可能的 TTS 设置。
6. 与 StoryBoard 初始化相关的配置。

纯脚本任务的视频分析详情中，参考视频区域显示为空状态：

```text
无参考视频，当前任务由脚本创建
```

但脚本、SRT、音色、Prompt、Task 配置能力仍然可用。

## 3. 左侧导航结构

左侧菜单建议：

```text
视频分析（口播）
任务列表（口播）
故事版（口播）
计费
```

「任务列表（口播）」是独立一级导航，不作为「故事版（口播）」下的二级菜单。

用户截图中红框位置适合放新的「任务列表（口播）」入口。

## 4. Task List 页面

### 4.1 页面目标

Task List 用于管理口播任务，而不是只管理 StoryBoard 产物。

用户可以在这里：

1. 创建视频分析任务。
2. 创建纯脚本任务。
3. 搜索任务。
4. 查看任务状态。
5. 进入视频分析详情。
6. 进入 StoryBoard 详情。
7. 软删除或归档任务。
8. 重试初始化失败的任务。

### 4.2 页面结构

建议结构：

1. 顶部标题：`口播任务列表`。
2. 主按钮：`新建任务`。
3. 创建方式按钮或菜单：`视频导入`、`脚本生成`。
4. 搜索框。
5. 状态筛选。
6. 创建方式筛选。
7. Task 表格。

### 4.3 表格字段

| 字段 | 说明 |
| --- | --- |
| 任务名称 | 用户可改名；默认从视频文件名、脚本首句或简单提示词生成 |
| Task / Session | 展示 `Task #x / Session #y` |
| 创建方式 | 视频导入、脚本生成 |
| 参考视频 | 有视频时显示文件名 / 时长；纯脚本任务显示「无参考视频」 |
| 脚本摘要 | 展示脚本前 1-2 行或摘要 |
| 状态 | 草稿、待配置、分析中、初始化中、可编辑、运行中、失败、已归档 |
| SRT 状态 | 未生成、已生成、已重写 |
| StoryBoard 状态 | 未生成、已生成、已编辑 |
| 音色状态 | 未选择、已选择、已克隆 |
| 素材完成度 | 音频、原图、新图、视频、终视频等完成计数 |
| 最近错误 | 最近失败原因 |
| 更新时间 | 默认排序 |
| 操作 | 视频分析、StoryBoard、重命名、复制、软删除、重试 |

### 4.4 操作按钮

每条任务至少有两个主操作：

| 操作 | 目标 |
| --- | --- |
| 视频分析 | 进入视频分析详情 / 脚本工作台 |
| StoryBoard | 进入 StoryBoard 明细 |

纯脚本任务点击「视频分析」时，进入无参考视频的视频分析详情。页面中不显示视频解析结果，但显示脚本、SRT、音色和重写能力。

纯脚本任务点击「StoryBoard」时，进入现有 StoryBoard 明细页。

## 5. 创建任务入口

### 5.1 新建任务菜单

点击「新建任务」后展示两种方式：

```text
视频导入创建
脚本生成创建
```

### 5.2 视频导入创建

视频导入创建沿用现有视频分析创建逻辑：

```text
上传 / 选择视频
填写或生成 Task 配置
创建 Session / workspace / Task
运行视频分析
生成 final_srt_frame_items.json
运行 00 / 04_01 / 04_02
```

如果当前视频分析创建和 StoryBoard 初始化不是同一步完成，可以保持现状，但 Task List 必须能展示这条任务，并能从任务进入视频分析详情和 StoryBoard 详情。

### 5.3 脚本生成创建

脚本生成创建不要求视频。

必填：

1. 脚本。
2. 至少一个可用的文本模型配置，供复杂提示词、SRT Rewrite 或 StoryBoard 使用。

可选：

1. 简单提示词。
2. 行业。
3. 人设。
4. 目标受众。
5. 视频公式。
6. 产品信息。
7. 约束条件。

创建后必须生成标准 SRT JSON：

```text
SessionOutput/subtitle/final_srt_frame_items.json
```

然后走同样的 `00`、`04_01`、`04_02`。

## 6. 脚本生成任务的数据流

### 6.1 脚本保存

脚本原文必须保存，建议路径：

```text
SessionOutput/subtitle/source_script.txt
```

也可以在 DB 中保留脚本文本或脚本摘要，便于列表搜索。长期建议 DB 保存可搜索字段，workspace 保存完整原文和派生产物。

### 6.2 脚本转 SRT JSON

脚本创建任务必须生成：

```text
SessionOutput/subtitle/final_srt_frame_items.json
```

建议最小结构：

```json
{
  "schema_version": "analysis_v1_final_srt_frame_items_0.1",
  "source_type": "script",
  "items": [
    {
      "srt_id": "srt_0001",
      "index": 1,
      "start": 0.0,
      "end": 4.0,
      "duration": 4.0,
      "dialogue": "第一句口播脚本。",
      "image_path": ""
    }
  ]
}
```

规则：

1. 普通脚本按换行和标点切句。
2. SRT 文本保留原始时间轴。
3. 没有时间轴时按文本长度估算时长。
4. `srt_id` 稳定生成，例如 `srt_0001`。
5. `image_path` 可以为空。

### 6.3 无参考视频的视频分析详情

脚本任务的视频分析详情应读取同一份 Task 和 SRT JSON，但不要求：

```text
reference_video_path
SessionContext/Video_Source.mp4
```

页面空态：

```text
无参考视频
当前任务由脚本创建，可继续进行脚本重写、音色选择、音色克隆和 StoryBoard 编辑。
```

### 6.4 00 的 script_only 支持

`00_PrepareSessionVariables` 当前视频分析链路会复制源视频到：

```text
SessionContext/Video_Source.mp4
```

脚本任务需要支持 `script_only` 输入模式。该模式下：

1. 不因缺少 `reference_video_path` 阻断。
2. 不要求 `Video_Source.mp4`。
3. 仍然写入 `SessionContext/Variables.json`。
4. 在 Variables 中明确记录输入模式。

建议字段：

```json
{
  "input_mode": "script_only",
  "source_script_path": "SessionOutput/subtitle/source_script.txt",
  "source_srt_items_path": "SessionOutput/subtitle/final_srt_frame_items.json",
  "source_video_path": "",
  "reference_video_original_path": ""
}
```

视频任务则为：

```json
{
  "input_mode": "video",
  "source_video_path": "SessionContext/Video_Source.mp4"
}
```

## 7. Task 状态设计

### 7.1 状态枚举

| 状态 | 含义 |
| --- | --- |
| 草稿 | 已创建但未提交必要输入 |
| 待配置 | 已有输入，但提示词或模型配置缺失 |
| 分析中 | 视频分析或脚本转 SRT 正在执行 |
| 初始化中 | `00`、`04_01`、`04_02` 正在执行 |
| 可编辑 | StoryBoard 已生成，可以进入明细编辑 |
| 运行中 | StoryBoard 明细页素材生成工具正在执行 |
| 失败 | 视频分析、脚本转 SRT 或初始化失败 |
| 已归档 | 软删除或归档 |

### 7.2 子状态

主状态之外，列表还应展示几个关键子状态：

| 子状态 | 来源 |
| --- | --- |
| SRT 状态 | 是否存在 `final_srt_frame_items.json` 和 `rewritten_srt_items.json` |
| StoryBoard 状态 | 是否存在 `srt_storyboard.json` 和 `koubo_storyboard_edit.json` |
| 音色状态 | 视频分析详情里的音色选择 / 克隆记录 |
| 素材完成度 | StoryBoard Working 文件和绑定状态 |

## 8. 接口建议

### 8.1 任务列表

```text
GET /api/koubo-tasks
```

或沿用：

```text
GET /api/koubo-storyboard/tasks
```

但返回语义应是 Task List，而不是只返回已有 StoryBoard 文件的列表。

返回示例：

```json
{
  "items": [
    {
      "task_id": 31,
      "session_id": 42,
      "title": "情绪内耗伤身",
      "create_mode": "script",
      "input_mode": "script_only",
      "status": "editable",
      "reference_video": null,
      "script_preview": "所有的乳腺结节、甲状腺包块...",
      "srt_status": "rewritten",
      "storyboard_status": "generated",
      "voice_status": "not_selected",
      "analysis_url": "/#/openclip/tasks/31",
      "storyboard_url": "/#/koubo-storyboard/tasks/31",
      "archived": false,
      "updated_at": 0
    }
  ]
}
```

### 8.2 创建视频任务

可以沿用现有视频分析创建接口。

如果为了统一入口，也可以提供：

```text
POST /api/koubo-tasks/create-from-video
```

### 8.3 创建脚本任务

建议新增：

```text
POST /api/koubo-tasks/create-from-script
```

请求：

```json
{
  "title": "",
  "script": "",
  "script_format": "plain",
  "rewrite_simple_prompt": "",
  "rewrite_final_prompt": "",
  "storyboard_simple_prompt": "",
  "storyboard_final_prompt": "",
  "business_context": {
    "industry": "",
    "persona": "",
    "target_audience": "",
    "video_formula": "",
    "product_info": "",
    "constraints": ""
  },
  "storyboard_quick_config": {
    "enabled": true,
    "target_scene_seconds": 8.0,
    "target_shot_seconds": 16.0,
    "split_tolerance_seconds": 2.0,
    "language_boundary_mode": "balanced"
  },
  "run_model_provider": "",
  "run_model_id": ""
}
```

响应：

```json
{
  "ok": true,
  "task_id": 31,
  "session_id": 42,
  "workspace_dir": "/Users/.../.opencrew/sessions/42/workspace",
  "status": "initializing"
}
```

### 8.4 软删除

```text
DELETE /api/koubo-tasks/{task_id}
```

行为：

1. 不物理删除 workspace。
2. 标记任务为 `archived`。
3. 写入 session event。
4. 默认列表不展示。

### 8.5 重试初始化

```text
POST /api/koubo-tasks/{task_id}/initialize/retry
```

行为：

1. 读取 Task DB 字段。
2. 按 `input_mode` 判断视频任务或脚本任务。
3. 视频任务复用已有视频分析产物。
4. 脚本任务重新生成标准 SRT JSON。
5. 重新运行 `00`、`04_01`、`04_02`。
6. 更新任务状态。

## 9. 前端详情入口

### 9.1 视频分析详情

任务行的「视频分析」按钮进入视频分析详情。

视频任务：

1. 展示参考视频。
2. 展示视频解析结果。
3. 展示 SRT、脚本重写、音色选择、音色克隆等能力。

脚本任务：

1. 不展示参考视频，显示无视频空态。
2. 展示脚本和标准 SRT JSON。
3. 继续复用脚本重写、音色选择、音色克隆等能力。

### 9.2 StoryBoard 详情

任务行的「StoryBoard」按钮进入现有 StoryBoard 明细页：

```text
/#/koubo-storyboard/tasks/:taskId
```

只有在 StoryBoard 已生成或初始化失败需要诊断时可进入。初始化中可以禁用，第一版建议禁用。

## 10. 前端模块化实现要求

### 10.1 新模块边界

Task List 前端必须作为新模块实现，不允许把主要页面逻辑继续塞进 App 主页面或既有大型页面组件。

建议模块目录：

```text
frontend/src/modules/koubo/KouboTaskList/
```

建议文件结构：

```text
KouboTaskList/
  index.jsx
  KouboTaskListPage.jsx
  KouboTaskListTable.jsx
  KouboTaskCreateMenu.jsx
  KouboTaskCreateFromVideoModal.jsx
  KouboTaskCreateFromScriptModal.jsx
  KouboTaskFilters.jsx
  kouboTaskListApi.js
  kouboTaskListModel.js
  kouboTaskListStatus.js
  styles/
    taskListPage.css
    taskListTable.css
    taskCreateModal.css
    taskStatusBadges.css
```

如果实际项目已有更合适的模块命名规范，可以沿用现有规范，但必须保持「任务列表（口播）」为独立模块。

### 10.2 文件大小控制

实现时需要控制单文件规模，避免形成新的大文件。

建议约束：

| 文件类型 | 建议上限 | 说明 |
| --- | --- | --- |
| 页面容器 | 300 行以内 | 只负责编排数据和子组件 |
| 表格组件 | 250 行以内 | 列定义、行操作、空态拆分 |
| 创建弹窗 | 300 行以内 | 视频创建和脚本创建拆成两个组件 |
| API 文件 | 200 行以内 | 只放接口调用和轻量转换 |
| 状态/模型工具 | 200 行以内 | 状态枚举、格式化、字段归一 |
| 单个 CSS 文件 | 220 行以内 | 按页面、表格、弹窗、状态徽标拆分 |

当组件超过建议上限时，应优先拆分为更小的领域组件，而不是继续扩大单文件。

### 10.3 CSS 拆分要求

CSS 不能集中到一个大的全局 CSS 文件。

要求：

1. Task List 样式放在模块自己的 `styles/` 目录。
2. 页面布局、表格、创建弹窗、状态徽标分别拆 CSS。
3. 不把 Task List 样式追加到 App 全局主 CSS。
4. 类名使用模块前缀，避免影响视频分析和 StoryBoard 现有页面。
5. 禁止用一个大型 CSS 覆盖所有 Koubo 页面。

建议类名前缀：

```text
koubo-task-list-
```

### 10.4 App 主页面接入方式

App 主页面或路由主文件只做路由注册和懒加载接入：

```text
route -> KouboTaskListPage
```

不在 App 主页面写：

1. Task List 表格列。
2. 创建弹窗状态。
3. 搜索筛选逻辑。
4. 接口请求细节。
5. 大段 Task List CSS。

### 10.5 后端复用边界

后台可以复用现有视频分析、Task、Session、workspace、`00`、`04_01`、`04_02` 能力。

但前端 Task List 是新模块。后端复用不意味着前端复用为大杂烩页面；前端只通过清晰 API 获取统一 Task List 数据，并把「视频导入创建」和「脚本生成创建」作为同一模块里的两个创建入口。

## 11. 数据字段建议

### 11.1 Task 主字段

建议新增或补齐：

| 字段 | 说明 |
| --- | --- |
| `create_mode` | `video` / `script` |
| `input_mode` | `video` / `script_only` |
| `task_title` | 任务列表名称 |
| `source_script_path` | 脚本源文件路径 |
| `source_script_preview` | 列表搜索和展示摘要 |
| `archived_at` | 软删除时间 |
| `initialize_status` | 初始化状态 |
| `initialize_error` | 最近初始化错误 |

### 11.2 复用字段

继续复用：

```text
reference_video_path
industry
persona
target_audience
product_info
constraints
video_formula
rewrite_simple_prompt
rewrite_final_prompt
storyboard_simple_prompt
storyboard_final_prompt
storyboard_quick_config_json
run_model_provider
run_model_id
```

### 11.3 workspace 产物

视频任务和脚本任务都应尽量产出同名标准文件：

```text
SessionOutput/subtitle/final_srt_frame_items.json
SessionOutput/subtitle/rewritten_srt_items.json
SessionOutput/storyboard/srt_storyboard.json
SessionOutput/storyboard/koubo_storyboard_edit.json
```

脚本任务额外保存：

```text
SessionOutput/subtitle/source_script.txt
```

## 12. 验收标准

### 12.1 Task List

1. 点击左侧「任务列表（口播）」进入口播任务列表。
2. 列表显示视频任务和脚本任务。
3. 列表支持按任务名、Task ID、Session ID、脚本内容搜索。
4. 每条任务有「视频分析」和「StoryBoard」两个详情入口。
5. 纯脚本任务的视频分析入口可打开，并显示无参考视频空态。
6. 软删除后默认列表不显示，但 workspace 不被删除。

### 12.2 视频创建

1. 视频导入创建仍然创建 Session / workspace / Task。
2. 视频解析生成标准 `final_srt_frame_items.json`。
3. 后续可进入视频分析详情和 StoryBoard 详情。

### 12.3 脚本创建

1. 不上传视频也可以创建任务。
2. 后端创建 Session / workspace / OpenCode session / Task。
3. Task 配置字段保存完整。
4. 脚本原文保存到 workspace。
5. 脚本生成标准 `final_srt_frame_items.json`。
6. `00` 支持 `script_only`，不因没有参考视频阻断。
7. `04_01` 成功生成 `rewritten_srt_items.json`。
8. `04_02` 成功生成 `srt_storyboard.json`。
9. 创建完成后停留在任务列表，状态显示「可编辑」。
10. 不自动运行 TTS、图片、视频、口型和最终合成工具。

### 12.4 能力复用

1. 脚本任务可以进入视频分析详情。
2. 脚本任务可以使用脚本重写。
3. 脚本任务可以使用音色选择。
4. 脚本任务可以使用音色克隆。
5. 脚本任务可以进入 StoryBoard 明细并继续生成素材。

### 12.5 前端模块化

1. Task List 页面由独立模块承载。
2. App 主页面只做路由接入，不承载 Task List 业务逻辑。
3. 视频创建弹窗和脚本创建弹窗拆分为独立组件。
4. CSS 拆分为多个模块样式文件，不存在一个 Task List 大 CSS。
5. Task List 样式不污染视频分析详情和 StoryBoard 明细页。

## 13. 待确认问题

1. 视频分析详情路由是否复用现有视频分析 Task 详情路由，还是新增一个统一 Task 详情页。
2. `script_only` 支持是改造 `00_PrepareSessionVariables`，还是新增一个 `00` 前置脚本准备工具。
3. 脚本全文是否进入 DB，还是 DB 只存摘要和 workspace 路径。
4. 脚本任务的音色克隆如果没有视频音频来源，默认应要求用户上传参考音频，还是允许仅选择现有音色。
