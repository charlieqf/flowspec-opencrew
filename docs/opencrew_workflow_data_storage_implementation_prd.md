# OpenCrew 工作流基础设施数据存储及实现 PRD

版本：v0.2

日期：2026-05-27

状态：基础设施标准草案。第 18 章为非规范附录，执行优先级以 `docs/opencrew_repo_improvement_plan.md` 为准。

## 1. 文档目标

本文定义 OpenCrew 后续所有工作流模块的通用基础设施标准，覆盖数据存储、OpenCode Session 绑定、Prompt 版本管理、Session Detail 客户状态页、Debug Console 系统调试页、事件可见性、文件存储与交付、API 和验收标准。

适用对象：

- 任意新增 Workflow。
- 任意 Workflow Task 页面。
- WorkflowAssistant。
- Session Detail 页面。
- Debug Console。
- Tool Library 调用链路。

业务模块样例：

- `OC - Analysis`
- `OC - Rebuild`
- `OC - StoryBoard`

这些业务模块只作为复现样例保留。本文不定义它们的业务产物、文件 schema、业务流程细节或具体交付格式；这些内容必须放在对应业务 PRD 中。

### 1.1 信任边界与威胁模型

当前阶段不引入完整用户鉴权体系，因此必须明确临时信任边界：

- 不可信入口：匿名 share token 页面、share token 事件 API、share token 文件 API、任何可被外部转发访问的公开链接。
- 有限可信入口：本地开发环境、主 UI、Debug Console、内部 Workflow 页面。它们默认只在受控本机或受控内网中使用。
- Debug Console 不是匿名能力。没有正式 auth 前，不应通过公网反代、share 链接或未受控隧道暴露 Debug Console。
- `visibility` / `event_scope` 是展示与分享边界，不是完整认证授权机制。它只能保证匿名 share 和客户页不看到 debug/internal 内容。
- 如果部署到多人或公网环境，必须在本 PRD 之外补充正式 auth、角色、访问控制和审计策略。

由此得到的 P0 安全目标是：即使没有完整 auth，匿名 share 入口也不能读取 debug/internal 事件、敏感文件、workspace 外文件或 secrets。

## 2. 背景与定位

OpenCrew 的定位是 OpenCode 之上的业务工作流控制层。

OpenCode 负责：

- 模型调用。
- Agent 会话。
- 持续对话上下文。
- 模型 provider / model 能力。
- 通过 Session 执行 prompt、Agent 和工具调用。

OpenCrew 负责：

- Workflow 定义。
- Task 创建和状态管理。
- Session 绑定和 workspace 管理。
- Simple Prompt / Final Prompt 生成与版本管理。
- Run Model / Prompt Model 选择。
- Attempt 运行记录。
- Tool Library 计划和调用治理。
- Session Detail 客户状态页。
- Debug Console 系统调试页。
- 文件存储、文件列表、交付物下载。
- 数据库主状态恢复。

因此，一个 OpenCrew 工作流不是一次性 API 调用，也不是孤立脚本，而是一个可恢复、可版本化、可观察、可分享的业务执行单元。

标准产品机制：

```text
新建 Workflow Task
  -> 创建或绑定 OpenCrew Session
  -> 创建或绑定 OpenCode Session
  -> 创建 workspace
  -> 采集参数
  -> 拼接 Simple Prompt
  -> 支持人工手动修改 Simple Prompt
  -> 使用同一 OpenCode Session 和选定模型生成 Final Prompt
  -> 保存一个或多个 Final Prompt Version
  -> 在同一 OpenCode Session 中持续对话
  -> 通过 Skill / Tool Library 调用工具集
  -> 写入数据库状态、session_events、workspace 文件
  -> Session Detail 呈现客户可见状态和交付物
  -> Debug Console 呈现系统调试和长任务调用状态
```

该机制是后续所有工作流复现的基础，不允许新增工作流绕过 Task / Session / OpenCode Session / Version / Attempt / Session Detail / Debug Console 的标准链路。

## 3. 核心概念

### 3.1 Workflow

Workflow 是一个可复用的工作流定义。

必须包含：

- `workflow_id`
- `name`
- `source`
- `task_adapter`
- Tool Library 配置
- Assistant 配置
- Plan schema
- Runner 配置

当前项目中 Workflow 定义主要在代码中维护，例如 `WorkflowAssistant/backend/workflow_assistant/routes.py` 的 `WORKFLOW_CONFIGS`。长期建议迁移为数据库或配置文件。

### 3.2 Task

Task 是某个 Workflow 的一次业务实例。

Task 必须保存可恢复主状态：

- 参数。
- Simple Prompt。
- Final Prompt。
- 当前 Final Prompt Version。
- 当前运行版本或 Skill / Intent Version。
- Prompt Model。
- Run Model。
- 当前状态。
- latest attempt。
- session 绑定。

### 3.3 Session

Session 是 Task 的运行上下文、事件容器和客户状态页入口。

Session 必须绑定：

- `session_id`
- `workspace_dir`
- `opencode_session_id`，如果该 Workflow 需要 OpenCode。
- `status`
- `created_at`
- `updated_at`
- `started_at`
- `finished_at`

### 3.4 OpenCode Session

OpenCode Session 是模型、Agent 和持续对话上下文。

OpenCrew 只保存 `opencode_session_id` 引用，不复制完整 OpenCode messages 作为主状态。

约束：

- 同一个 Task 的 Simple Prompt -> Final Prompt 必须使用该 Task 绑定的 OpenCode Session。
- Final Prompt 后续引导 Skill / Tool Library 调用必须继续使用同一个 OpenCode Session。
- 切换 Final Prompt Version 不应创建新的 OpenCode Session。
- 运行时必须显式注入当前 Final Prompt Version，不能依赖 OpenCode 历史对话“记得”当前版本。

### 3.5 Workspace

Workspace 是 Session 的文件根目录。

Workspace 用于保存：

- 用户上传文件。
- 工具输入文件。
- 工具输出文件。
- 中间文件。
- 最终交付物。
- 审计文件。
- 可下载包。

Workspace 不作为 Task 主状态源。Task 主状态必须在数据库中可恢复。

### 3.6 Version

Version 是可切换、可复用、可追溯的 Prompt / Skill / Intent / Runtime 快照。

Final Prompt 必须支持多版本。

每个 Final Prompt Version 必须保存：

- 生成时的参数快照。
- Simple Prompt。
- Final Prompt。
- Prompt Model。
- notes / name。
- created_at。

### 3.7 Attempt

Attempt 是一次运行记录。

每次 Run / Rerun 必须创建 Attempt，并记录：

- attempt no。
- status。
- prompt version。
- runtime version。
- run model。
- started_at。
- finished_at。
- summary。
- result index reference，泛指结果索引，不定义业务格式。

## 4. 数据存储边界

### 4.1 必须进入数据库

- Workflow 定义或配置引用。
- Task 主状态。
- Session 主状态。
- OpenCode Session 引用。
- Simple Prompt。
- Final Prompt。
- Prompt / Skill / Intent / Runtime Version。
- Attempt。
- Prompt Model / Run Model 选择。
- Workflow Plan。
- session event。
- 文件索引。
- 分享 token 和权限。

### 4.2 不进入数据库作为主状态

- OpenCode 完整 message 列表。
- OpenCode message part 细节。
- OpenCode SSE 原始事件流。
- OpenCode provider 全量动态响应。
- 大文件内容。
- 媒体文件内容。
- base64 / blob / bytes。
- workspace 中的具体业务产物内容。

### 4.3 可进入数据库作为索引或摘要

- 文件路径。
- 文件大小。
- 文件更新时间。
- 文件 origin。
- 可下载标记。
- 运行摘要。
- 结果索引 JSON。
- 错误摘要。
- Debug event 摘要。

## 5. 数据库模型标准

### 5.1 通用表

已有通用表：

- `sessions`
- `session_events`
- `session_files`
- `session_shares`
- `workflow_plans`

建议新增或标准化：

- `workflow_definitions`
- `workflow_tasks`，可选，用于抽象通用 Task 主表。
- `workflow_versions`，可选，用于统一版本表。
- `workflow_attempts`，可选，用于统一运行记录。

当前项目可以继续使用业务专属 task 表，但字段必须符合本文标准。

### 5.2 Task 表必需字段

每个 Workflow 的 task 表必须具备：

- `id`
- `session_id`
- `status`
- `title`，可直接字段或通过 sessions.title 派生。
- `simple_prompt`
- `final_prompt`
- `current_prompt_version_id`
- `current_runtime_version_id`，名称可按业务替换为 `current_skill_version_id`、`current_intent_version_id` 等。
- `latest_attempt_id`
- `prompt_model_provider`
- `prompt_model_id`
- `run_model_provider`
- `run_model_id`
- `business_config_json`，推荐。
- `runtime_config_json`，推荐。
- `created_at`
- `updated_at`

### 5.3 Version 表必需字段

每个 Workflow 至少要有一种版本表。

必需字段：

- `id`
- `task_id`
- `name`
- `notes`
- `snapshot_json`
- `simple_prompt`
- `final_prompt`
- `runtime_content`，如 Skill / Intent / Plan 文本。
- `prompt_model_provider`
- `prompt_model_id`
- `created_at`

要求：

- 保存版本时保存完整业务快照。
- 加载版本时恢复 Task 当前字段。
- 删除当前版本时必须明确 fallback 策略。

### 5.4 Attempt 表必需字段

- `id`
- `task_id`
- `session_id`
- `attempt_no`
- `status`
- `prompt_version_id`
- `runtime_version_id`
- `run_model_provider`
- `run_model_id`
- `summary`
- `result_index_json`
- `started_at`
- `finished_at`
- `created_at`

当前实现兼容说明：OpenClip / OC-Analysis 现有字段名为 `result_manifest_json`。目标标准使用 `result_index_json`，迁移期应提供映射层或新增字段，不应直接破坏历史 attempt 读取。

### 5.5 session_events 标准

`session_events` 是 Session Detail 和 Debug Console 的共同事件来源，但两者使用不同的过滤和展示规则。

现有字段：

- `id`
- `session_id`
- `kind`
- `payload`
- `created_at`

推荐在 payload 中统一包含：

- `workflow_id`
- `task_id`
- `session_id`
- `attempt_id`
- `visibility`
- `event_scope`
- `severity`
- `family`
- `status`
- `message`
- `detail`
- `provider`
- `model`
- `tool_id`
- `step_id`
- `elapsed_seconds`
- `output_path`

推荐 `visibility`：

- `public`：可进入 Session Detail，客户可见。
- `internal`：内部可见，默认不在客户页面突出展示。
- `debug`：只进入 Debug Console。

推荐 `event_scope`：

- `session_detail`
- `debug_console`
- `both`

推荐 `severity`：

- `info`
- `warning`
- `error`

兼容策略：

- 如果没有 `visibility`，由 `kind` 推断。
- `user.message`、`assistant.final` 默认 `public`。
- 模型调用、工具调用、provider 调用默认 `debug`。
- 生命周期状态可根据内容为 `public` 或 `both`。

### 5.6 当前项目实际数据库结构盘点

本节用于保留当前实现经验，后续新增工作流必须理解这些现有表的职责，避免重复造表或把主状态写到 workspace 文件中。

#### 5.6.1 `sessions`

职责：OpenCrew 的通用 Session 容器，是客户状态页、Debug Console、workspace、OpenCode Session 引用的中心表。

当前字段：

- `id`
- `source`
- `group_id`
- `sender_id`
- `sender_name`
- `title`
- `command_text`
- `status`
- `opencode_session_id`
- `workspace_dir`
- `share_token`
- `last_summary`
- `created_at`
- `updated_at`
- `started_at`
- `finished_at`

实现要求：

- 每个 Workflow Task 必须能追溯到一个 `sessions.id`。
- `workspace_dir` 必须指向该 Session 的唯一 workspace 根目录。
- `opencode_session_id` 是 OpenCode 的引用，不是 OpenCrew 自己的消息存储。
- Session status 用于 Session Detail 的客户状态展示，也可作为 Debug Console 的分组状态。
- 删除 Session 时必须同步清理 `session_events`、`session_files`、`session_shares` 和 workspace 目录。

#### 5.6.2 `session_events`

职责：Session 的事件流和可恢复历史，是 Session Detail 与 Debug Console 的共同底座。

当前字段：

- `id`
- `session_id`
- `kind`
- `payload`
- `created_at`

实现要求：

- `kind` 必须稳定可分类，例如 `user.message`、`assistant.final`、`session.status`、`workflow.plan.confirmed`、`tool_call.completed`。
- `payload` 必须是 JSON string。
- 后续新增字段优先放入 payload 并由 presenter 解析，避免频繁数据库迁移。
- 客户可见事件和 debug 事件必须通过 `visibility` / `event_scope` 区分。
- 长任务关键节点必须落库，不能只依赖前端本地 Debug 事件。

#### 5.6.3 `session_files`

职责：workspace 文件索引，支撑 Session Detail 文件列表、下载和 ZIP。

当前字段：

- `id`
- `session_id`
- `path`
- `kind`
- `size`
- `origin`
- `downloadable`
- `updated_at`

约束：

- `session_id + path` 唯一。
- `path` 必须是 workspace 相对路径。
- `origin` 建议使用 `uploaded`、`generated`、`system` 等稳定值。
- `downloadable=0` 的文件不应在客户交付列表中突出展示。
- 文件内容不入库，只入库索引和元数据。

#### 5.6.4 `session_shares`

职责：Session Detail 的分享链接和权限控制。

当前字段：

- `id`
- `session_id`
- `token`
- `scope`
- `expires_at`
- `created_at`

实现要求：

- 客户分享页必须通过 share token 访问。
- share scope 必须限制可见内容，不能暴露 Debug Console。
- share 过期后必须返回明确错误。

#### 5.6.5 `workflow_plans`

职责：WorkflowAssistant 的执行计划草稿、确认状态和审计记录。

当前字段：

- `id`
- `workflow_id`
- `task_id`
- `session_id`
- `status`
- `plan_json`
- `created_by_message_id`
- `confirmed_by_message_id`
- `created_at`
- `updated_at`
- `confirmed_at`

实现要求：

- Plan 是 OpenCrew 数据库主状态，不应只写回 OpenCode 对话。
- `plan_json` 必须包含 `schema`、`workflow_id`、`task_id`、`session_id`、`steps`。
- 高成本、长耗时、模型、Agent、外部服务调用必须确认。
- 确认 Plan 后可以向 OpenCode 写入状态更新，但 OpenCode 写入失败不能回滚数据库确认状态。

#### 5.6.6 当前业务专属 Task / Version / Attempt 表样例

当前项目中已有业务专属表，例如：

- `openclip_tasks`
- `openclip_prompt_versions`
- `openclip_skill_versions`
- `openclip_attempts`
- `oc_rebuild_tasks`
- `oc_rebuild_prompt_versions`
- `oc_rebuild_attempts`
- `openflow_analysis_runs`，历史兼容，不建议作为新模板。

这些表的价值是沉淀了当前工作流的实现经验：Task 主状态在数据库，Prompt / Skill / Intent 等可运行内容进入版本表，Attempt 记录每次运行。后续新 Workflow 可以继续使用业务专属表，但必须满足本文的通用字段标准。

业务专属表的通用规则：

- 必须有 `session_id`。
- 必须有 `status`。
- 必须有当前 Prompt / Runtime 版本指针。
- 必须有 latest attempt 指针。
- 必须有 Prompt Model / Run Model 字段。
- 必须有 `created_at`、`updated_at`。
- Version 表必须保存完整快照，而不只是最终文本。
- Attempt 表必须保存 run model、状态、开始和结束时间。

### 5.7 数据库存储边界矩阵

| 数据 | 主存储 | 可复制到 workspace | 可出现在 Session Detail | 可出现在 Debug Console | 说明 |
| --- | --- | --- | --- | --- | --- |
| Workflow 定义 | DB / config | 否 | 可展示名称 | 可展示 | 配置源应稳定 |
| Task 参数 | DB | 可作为运行输入副本 | 可展示摘要 | 可展示摘要 | DB 是主状态 |
| Simple Prompt | DB | 可写运行输入副本 | 可展示摘要或当前版本 | 可展示摘要 | 不只存在 OpenCode |
| Final Prompt | DB | 可写运行输入副本 | 可展示摘要或版本 | 可展示摘要 | 支持多版本 |
| Prompt Version | DB | 可写审计副本 | 可展示当前版本 | 可展示版本 ID | Version 是快速切换基础 |
| Run Model | DB | 可写审计副本 | 可展示 | 必须展示 | 运行追溯必需 |
| OpenCode messages | OpenCode | 否 | 可 live 读取展示消息 | 可 live 读取上下文 | 不复制为主状态 |
| session event | DB | 否 | public 事件 | debug/internal 事件 | 通过 visibility 区分 |
| workspace 文件 | Workspace | 原始存储 | 可下载 | 可展示路径 | DB 只存索引 |
| 文件索引 | DB | 否 | 必须展示 | 可展示 | `session_files` |
| Debug payload | DB event payload | 否 | 默认不展示 | 必须展示摘要 | 必须 sanitize |
| 交付物 | Workspace | 原始存储 | 必须可下载 | 可展示路径 | 具体 schema 属于业务 PRD |

## 6. Session Detail vs Debug Console

### 6.1 核心区别

`Session Detail` 是客户状态页。

`Debug Console` 是系统调试页。

两者可以读取同一个 `session_events`，但必须使用不同的数据过滤、展示语言和信息密度。

### 6.2 Session Detail 客户状态页

定位：

- 给客户、用户、IM 会话参与者查看 Session 状态。
- 支持 WhatsApp、WeCom、其他 IM 工具中分享网页链接。
- 呈现 Session 的最终状态、消息记录、文件列表和交付物。
- 用户可以下载文件和交付物。

必须展示：

- Session ID。
- Task 标题。
- 当前状态。
- 创建时间。
- 更新时间。
- 运行耗时。
- 用户消息。
- Assistant 消息。
- 流式消息合并后的最终展示。
- 文件列表。
- 文件下载。
- ZIP 下载。
- 上传入口，如果该 Session 允许上传。
- 简化工作日志。

可以展示：

- share URL。
- 可读的状态变更。
- 可读的错误摘要。
- 文件数量和文件更新时间。

不应展示：

- Debug-only payload。
- provider 内部细节。
- 完整模型调用参数。
- 完整工具调用参数。
- token、成本、内部请求 ID，除非已转为客户可理解摘要。
- base64 / bytes / blob。
- 过长 JSON。
- 内部错误堆栈。
- secrets、API key、auth 信息。

Session Detail 事件过滤：

- 显示 `visibility=public`。
- 显示 `event_scope=session_detail` 或 `event_scope=both`。
- 对缺失 `visibility` 的历史事件，允许展示 `user.message`、`assistant.final`、必要 `session.status`、必要 `session.created`、必要 `session.completed`、必要 `session.failed`。
- 工作日志必须用人类可读摘要，不直接暴露原始 payload。

当前代码样例：

- `frontend/src/App.jsx` 中 `renderSessionWorkspace()`。
- `displayTaskMessages` 聚合用户消息、assistant streaming 和 final 消息。
- `displaySystemLogEvents` 展示工作日志。
- `loadTaskFiles` 展示文件列表。

后端 API 样例：

- `GET /api/session-tasks/{session_id}`
- `GET /api/sessions/{session_id}/events`
- `GET /api/sessions/{session_id}/events/stream`
- `GET /api/session-tasks/{session_id}/files`
- `GET /api/session-tasks/{session_id}/raw/{file_path}`
- `GET /api/session-tasks/{session_id}/files.zip`

### 6.3 Debug Console 系统调试页

定位：

- 给开发者、AI Agent、系统维护者排查运行状态。
- 展示长任务调用开始、心跳、结束、失败。
- 展示模型调用、Agent 调用、工具调用、provider 调用、文件 I/O、网络错误、系统异常。
- 帮助 AI 判断当前任务卡在哪里、哪个工具失败、哪个模型调用异常。

必须展示：

- session 分组。
- system 级事件。
- requested / queued / calling / running / heartbeat / completed / failed / timeout / cancelled。
- provider。
- model。
- tool id。
- step id。
- request id。
- api call id。
- elapsed time。
- output path。
- error detail。
- 可展开 JSON 摘要。

不负责：

- 客户交付查看。
- 文件交付下载主入口。
- 最终业务结果呈现。
- IM 分享页面。
- 客户友好的消息合并展示。

Debug Console 事件过滤：

- 显示 `visibility=debug`、`visibility=internal`。
- 显示 `event_scope=debug_console` 或 `event_scope=both`。
- 可以显示 public 事件作为上下文，但展示方式必须区别于客户页。
- payload 必须 sanitize 和截断。

当前代码样例：

- `frontend/src/debug/DebugConsole.jsx`
- `frontend/src/debug/debugStore.js`
- `frontend/src/debug/debugAdapter.js`

当前 Debug Console 能力：

- 读取 `/api/sessions/{session_id}/events` 历史。
- 连接 `/api/sessions/{session_id}/events/stream`。
- 按 session 分组。
- 对 payload 进行截断。
- 合并 live events 和 history events。
- 展示 provider、model、family、status、output path。

### 6.4 两者共享与隔离

共享：

- `sessions`。
- `session_events`。
- `session_files`。
- workspace 文件读取 API。
- SSE event stream。

隔离：

- Session Detail 使用客户可见 presenter。
- Debug Console 使用调试 presenter。
- Session Detail 以最终状态和交付物为中心。
- Debug Console 以调用链、错误、心跳和 payload 摘要为中心。
- Session Detail 不应被 Debug 噪音污染。
- Debug Console 不应替代客户交付页。

## 7. Prompt 与版本标准

### 7.1 Simple Prompt

Simple Prompt 由参数采集、模板拼接和人工编辑共同生成。

要求：

- 当前 Simple Prompt 必须存数据库。
- Simple Prompt 可以写入 workspace 作为运行输入副本，但 workspace 不是主状态。
- 修改 Simple Prompt 后必须保存回 Task。

### 7.2 Final Prompt

Final Prompt 由 Simple Prompt 通过 OpenCode Session 调用模型生成。

要求：

- 使用当前 Task 绑定的 OpenCode Session。
- 使用当前选择的 Prompt Model 或 Run Model fallback。
- 生成结果必须写入数据库。
- 不允许 Final Prompt 只存在 OpenCode message 中。
- 生成过程必须写 `session_events`，并进入 Debug Console。

### 7.3 Final Prompt Version

要求：

- 同一个 Task / Session 支持多个 Final Prompt Version。
- 切换版本不创建新 OpenCode Session。
- 后续 Skill / Tool Library 调用必须使用当前选中版本。
- 版本切换事件进入 `session_events`。
- Session Detail 可以展示当前版本摘要。
- Debug Console 可以展示版本切换和后续调用使用的版本 ID。

### 7.4 Prompt Model 与 Run Model

Prompt Model：

- 用于 Simple Prompt -> Final Prompt。
- 用于 prompt refine。
- 用于生成 Skill / Intent 文本。

Run Model：

- 用于执行当前 Final Prompt。
- 用于持续对话中的正式运行。
- 用于 Tool Library 调用链中的模型步骤。

如果某个工作流只暴露一个模型选择，则默认落到 Run Model，并作为 Prompt Model fallback。

## 8. Tool Library 调用标准

Tool Library 是工作流的工具集执行层。

基础设施 PRD 只规定调用治理，不规定具体工具业务。

要求：

- 工具必须在 registry 中声明。
- 工具必须声明 id、name、description、args schema、cost level、是否使用模型、主要输出。
- 高成本、长耗时、模型、Agent 或外部服务调用必须可确认。
- 工具调用必须写 `session_events`。
- 工具调用必须进入 Debug Console。
- 工具输出文件写入 workspace。
- 工具输出文件如果面向客户交付，必须可在 Session Detail 下载。

## 9. 文件存储与交付

Workspace 是 Session 的文件存储根目录。

通用规则：

- 所有文件路径对前端暴露时必须是 workspace 相对路径。
- raw file API 必须限制在 workspace 内。
- 客户可下载文件必须通过 Session Detail 呈现。
- ZIP 下载必须支持当前目录或完整 workspace 打包。
- 上传文件必须写入 workspace 并同步文件索引。
- 文件索引写入 `session_files`。
- 具体业务产物目录和 schema 不在本 PRD 定义。

文件 API：

- `GET /api/session-tasks/{session_id}/files?path=...`
- `GET /api/session-tasks/{session_id}/raw/{file_path}`
- `GET /api/session-tasks/{session_id}/files.zip?path=...`
- `POST /api/session-tasks/{session_id}/upload?path=...`

Session Detail 文件展示要求：

- 支持目录浏览。
- 支持单文件打开或下载。
- 支持 ZIP 下载。
- 支持上传，如果该 Session 允许。
- 不展示隐藏文件。
- 不展示 secrets。

## 10. API 标准

### 10.1 Workflow Task API

每个 Workflow 必须提供：

- `GET /api/<workflow>/tasks`
- `POST /api/<workflow>/tasks`
- `GET /api/<workflow>/tasks/{task_id}`
- `DELETE /api/<workflow>/tasks/{task_id}`
- `PUT /api/<workflow>/tasks/{task_id}/config`

创建 Task 必须完成的后端动作：

1. 创建 task row，状态为 `draft` 或 `created`。
2. 创建 session row，并写入 `source`、`title`、`status`、`workspace_dir`。
3. 创建 workspace 目录。
4. 如该工作流需要 OpenCode，创建或绑定 OpenCode Session，并保存 `opencode_session_id`。
5. 写入 `session.created` 或 `workflow.task.created` 事件。
6. 返回 task、session、workspace 和当前模型选择。

更新 config 必须完成的后端动作：

1. 校验 task 存在且未删除。
2. 保存业务参数快照到 task 表。
3. 重新生成或保存 Simple Prompt。
4. 保留当前 Final Prompt Version，除非请求明确要求重置。
5. 更新 `updated_at`。
6. 写入 `workflow.config.updated` 事件，默认 `visibility=internal`、`event_scope=both`。

删除 Task 必须完成的后端动作：

1. 查找 task 绑定的 session。
2. 删除或软删除 task row。
3. 删除或标记 task versions。
4. 删除或保留 attempts，策略必须在业务 PRD 中明确。
5. 删除 `sessions` 及其 events、files、shares。
6. 删除 workspace 目录，必须限制在允许的 workspace 根目录内。
7. 如果 OpenCode Session 没有删除 API，则只删除 OpenCrew 引用，不尝试伪造删除。

删除策略要求：

- 客户可见产品中建议软删除 Task，保留审计。
- 本地开发或临时运行可以硬删除，但必须明确 API 行为。
- 无论软删除或硬删除，都不能留下 Session Detail 中可打开但无主状态的孤儿 session。

### 10.2 Version API

每个可版本化对象必须提供：

- save draft
- save version
- load version
- delete version
- update current version

保存 Final Prompt Version 请求至少包含：

- `task_id`
- `name`
- `notes`
- `simple_prompt`
- `final_prompt`
- `snapshot_json`
- `prompt_model_provider`
- `prompt_model_id`

保存 Version 必须完成的后端动作：

1. 读取当前 task 主状态。
2. 生成完整 snapshot，包含参数、Simple Prompt、Final Prompt、模型选择和 runtime 配置。
3. 插入 version row。
4. 将 task 当前 version 指针切换到新 version。
5. 写入 `workflow.version.saved` 事件。

加载 Version 必须完成的后端动作：

1. 校验 version 属于该 task。
2. 将 version 中的 Simple Prompt / Final Prompt / runtime 内容恢复到 task 当前字段。
3. 更新当前 version 指针。
4. 不创建新的 OpenCode Session。
5. 写入 `workflow.version.loaded` 事件。

删除 Version 的 fallback 策略：

- 删除非当前版本：直接删除或软删除。
- 删除当前版本且存在其他版本：切换到最近创建的可用版本。
- 删除当前版本且不存在其他版本：清空 current version 指针，但不得清空 task 的当前 Simple Prompt / Final Prompt，除非请求明确要求。
- 删除版本不能删除历史 attempt 引用；历史 attempt 可保留已删除版本 ID 和快照摘要。

### 10.3 Run API

每个可运行 Workflow 必须提供：

- `POST /api/<workflow>/tasks/{task_id}/run`
- `POST /api/<workflow>/tasks/{task_id}/rerun`
- `GET /api/<workflow>/tasks/{task_id}/attempts`
- `GET /api/<workflow>/tasks/{task_id}/session-events`

Run 请求至少包含或可从 task 恢复：

- `task_id`
- `session_id`
- `prompt_version_id`
- `runtime_version_id`
- `run_model_provider`
- `run_model_id`
- `final_prompt`
- `workspace_dir`

Run 必须完成的后端动作：

1. 校验 task、session、workspace 存在。
2. 校验当前 Final Prompt 或 runtime 内容存在。
3. 创建 attempt，状态为 `queued` 或 `running`。
4. 更新 task `latest_attempt_id` 和 `status`。
5. 更新 session `status`、`started_at`。
6. 写入 `attempt.created` 和 `attempt.running` 事件。
7. 使用当前 task 绑定的 `opencode_session_id` 执行，不隐式创建新 OpenCode Session。
8. 将工具输入、输出和审计文件写入 workspace。
9. 同步 `session_files` 文件索引。
10. 成功时更新 attempt、task、session 为 completed，并写入 public completion 事件。
11. 失败时更新 attempt、task、session 为 failed，并写入 public 错误摘要和 debug 错误详情。

Rerun 必须创建新的 attempt，不得覆盖上一轮 attempt。

Attempt 状态推荐：

- `queued`
- `running`
- `completed`
- `failed`
- `cancelled`
- `timeout`

Attempt 与文件关系：

- 文件可以位于同一 session workspace。
- 文件索引应能通过 `origin`、`kind` 或 payload 追溯到 attempt。
- Session Detail 默认展示最新 attempt 的主要交付物，也可以让用户查看历史文件。

### 10.4 Session Detail API

通用 Session API：

- `GET /api/session-tasks`
- `GET /api/session-tasks/{session_id}`
- `DELETE /api/session-tasks/{session_id}`
- `POST /api/session-tasks/{session_id}/cancel`
- `POST /api/session-tasks/{session_id}/rerun`
- `GET /api/sessions/{session_id}/events`
- `GET /api/sessions/{session_id}/events/stream`
- `GET /api/session-tasks/{session_id}/files`
- `GET /api/session-tasks/{session_id}/raw/{file_path}`
- `GET /api/session-tasks/{session_id}/files.zip`
- `POST /api/session-tasks/{session_id}/upload`

`GET /api/session-tasks/{session_id}` 返回必须包含：

- session 基本信息。
- 关联 task 摘要。
- workflow 标识。
- status。
- title。
- created / updated / started / finished 时间。
- `opencode_session_id` 是否存在，客户页不必展示原始值。
- 可分享状态。
- 最新摘要。

`GET /api/sessions/{session_id}/events` 要求：

- 支持返回历史事件。
- 支持按 kind、visibility、event_scope 过滤，至少后端或前端之一必须过滤。
- 返回顺序稳定，默认按 `created_at`、`id` 升序。
- 不返回 secrets。
- 大 payload 必须截断或只返回摘要。

`GET /api/sessions/{session_id}/events/stream` 要求：

- SSE 事件必须携带 event id 或可排序时间。
- 断线重连后前端可以通过历史事件补齐。
- stream 不是唯一事件源，关键事件必须已写入数据库。

文件 API 要求：

- 所有路径参数必须 decode 后做 safe join。
- path 为空时返回 workspace 根目录文件列表。
- raw 下载必须拒绝目录、隐藏文件、路径逃逸和敏感文件。
- zip 下载必须限制在 workspace 内，并可排除隐藏文件和敏感文件。
- upload 必须限制大小、路径、文件名和覆盖策略。

### 10.5 Debug Console API

Debug Console 复用：

- `GET /api/sessions/{session_id}/events`
- `GET /api/sessions/{session_id}/events/stream`

要求：

- 后端必须写足够的 debug event。
- 前端 Debug Console 负责 sanitize、分组和展开。
- 关键 debug event 必须落库，不能只用前端本地事件。

### 10.6 WorkflowAssistant API

通用 API：

- `GET /api/workflows/{workflow_id}/tasks/{task_id}/assistant/bootstrap`
- `GET /api/workflows/{workflow_id}/tasks/{task_id}/assistant/messages`
- `GET /api/workflows/{workflow_id}/tasks/{task_id}/assistant/plan`
- `PUT /api/workflows/{workflow_id}/tasks/{task_id}/assistant/plan`
- `POST /api/workflows/{workflow_id}/tasks/{task_id}/assistant/plan/confirm`
- `POST /api/workflows/{workflow_id}/tasks/{task_id}/assistant/message`
- `POST /api/workflows/{workflow_id}/tasks/{task_id}/assistant/abort`
- `GET /api/workflows/{workflow_id}/tasks/{task_id}/assistant/events`

边界：

- messages 来自 OpenCode。
- plan 来自 OpenCrew 数据库。
- events 合并 OpenCode live events 和 OpenCrew `session_events`。

WorkflowAssistant bootstrap 必须返回：

- `workflow_id`
- `task_id`
- `session_id`
- `opencode_session_id`
- 当前 task status。
- 当前 plan。
- 当前模型选择。
- 是否存在未确认高成本步骤。

Assistant message 发送要求：

- 必须使用 task 绑定的 `opencode_session_id`。
- 用户消息可以写入 OpenCode messages。
- 对 OpenCrew 主状态有影响的结果必须单独写入数据库。
- 如果 message 生成了 plan，必须写入 `workflow_plans`。
- 如果 message 触发长任务，必须创建 attempt 或等价运行记录。

Plan confirm 要求：

- 只能确认属于当前 task 和 session 的 plan。
- 确认时写入 `confirmed_at` 和 `confirmed_by_message_id`。
- 确认后写入 public 或 internal 事件。
- 确认后执行工具链时写 debug 事件。

### 10.7 事件 kind 命名标准

推荐命名方式：`domain.object.action` 或 `domain.action.status`。

通用 kind：

- `session.created`
- `session.status`
- `session.completed`
- `session.failed`
- `workflow.task.created`
- `workflow.config.updated`
- `workflow.prompt.generated`
- `workflow.version.saved`
- `workflow.version.loaded`
- `workflow.version.deleted`
- `workflow.plan.created`
- `workflow.plan.confirmed`
- `attempt.created`
- `attempt.running`
- `attempt.heartbeat`
- `attempt.completed`
- `attempt.failed`
- `model.requested`
- `model.calling`
- `model.completed`
- `model.failed`
- `tool.requested`
- `tool.running`
- `tool.heartbeat`
- `tool.completed`
- `tool.failed`
- `file.created`
- `file.indexed`
- `error.public`
- `error.debug`

Event payload 最小结构：

```json
{
  "workflow_id": "workflow-id",
  "task_id": "task-id",
  "session_id": "session-id",
  "attempt_id": "attempt-id-or-null",
  "visibility": "public|internal|debug",
  "event_scope": "session_detail|debug_console|both",
  "severity": "info|warning|error",
  "family": "workflow|model|tool|file|error|session",
  "status": "running|completed|failed",
  "message": "human readable summary"
}
```

Sanitization 要求：

- 不写入 API key、cookies、auth header。
- 不写入完整 request body，如果其中包含用户文件或大 payload。
- 对 prompt 文本可写摘要，完整 Prompt 以 Task / Version 表为准。
- 对异常堆栈只写 debug 事件，客户页只显示错误摘要。

## 11. 前端绑定标准

### 11.1 Workflow 页面

Workflow 页面负责：

- 参数采集。
- Simple Prompt 编辑。
- Final Prompt 生成。
- Version 切换。
- Run / Rerun。
- 当前 Task 状态展示。
- 跳转 Session Detail。
- 激活 Debug Console 当前 session。

Workflow 页面不应承担：

- Debug Console 的内部分组逻辑。
- Session Detail 的客户交付视图。
- OpenCode message 的完整重放存储。

### 11.2 Session Detail 页面

绑定来源：

- `sessionTaskDetail`
- `sessionEvents`
- `sessionTaskFiles`
- `sessionTaskRawFileUrl`
- `sessionTaskZipUrl`

展示重点：

- 最终状态。
- 消息记录。
- 文件交付。
- 可读工作日志。

### 11.3 Debug Console

绑定来源：

- `sessionEvents`
- `sessionScopedEventStreamUrl`
- 前端本地 debug events，作为即时增强。

展示重点：

- 调用链。
- 状态变化。
- 心跳。
- 错误。
- payload 摘要。

## 12. 新工作流接入流程

1. 定义 `workflow_id`。
2. 定义 Task 表或接入通用 Task 表。
3. 定义 Version 表。
4. 定义 Attempt 表。
5. 创建 Session。
6. 创建 workspace。
7. 创建或绑定 OpenCode Session。
8. 实现参数采集。
9. 实现 Simple Prompt 生成和人工编辑。
10. 实现 Final Prompt 生成。
11. 实现 Final Prompt Version 管理。
12. 接入 Tool Library。
13. 接入 Run / Rerun。
14. 写入 public/internal/debug session events。
15. 接入 Session Detail。
16. 接入 Debug Console。
17. 支持文件列表和交付物下载。
18. 编写业务 PRD 定义具体产物和文件 schema。

### 12.1 后端接入详细流程

新工作流后端必须按以下顺序接入：

1. 定义 workflow adapter，明确 `workflow_id`、展示名称、Task 类型、Assistant 能力和 Tool Library 能力。
2. 建立 Task 表或接入通用 Task 表。
3. 建立 Version 表，用于 Final Prompt 和 runtime 内容版本化。
4. 建立 Attempt 表，用于每次运行记录。
5. 在 bootstrap / migration 中创建表和补列。
6. 实现 repository，所有写数据库动作集中在 repository 层。
7. 实现 create task API，并同时创建 session、workspace、OpenCode Session 引用。
8. 实现 config save API，保存参数和 Simple Prompt。
9. 实现 generate final prompt API，使用同一 OpenCode Session 调模型，并保存结果。
10. 实现 save / load / delete version API。
11. 实现 run / rerun API，创建 attempt 并写事件。
12. 实现 file indexing，确保 workspace 输出可进入 Session Detail。
13. 实现 delete API，避免孤儿 session、孤儿 workspace、孤儿 versions。
14. 接入 WorkflowAssistant bootstrap、plan、message、confirm。
15. 接入 Debug Console 所需事件。

### 12.2 前端接入详细流程

新工作流前端必须按以下顺序接入：

1. 新增 Workflow 页面入口和路由。
2. 实现 Task list 和 Task detail 加载。
3. 实现参数表单和 config save。
4. 实现 Simple Prompt 编辑区。
5. 实现 Final Prompt 生成按钮和结果展示。
6. 实现版本列表、保存版本、切换版本、删除版本。
7. 实现 Prompt Model / Run Model 选择。
8. 实现 Run / Rerun 按钮和 attempt 状态展示。
9. 实现跳转 Session Detail。
10. 实现激活 Debug Console 当前 session。
11. 接入 WorkflowAssistant Drawer。
12. 刷新页面后必须能从数据库恢复当前 Task 状态，不依赖前端 memory。

### 12.3 OpenCode 接入详细流程

OpenCode 接入必须满足：

1. 后端启动时可以发现 OpenCode CLI server 或配置的 OpenCode endpoint。
2. 如果 OpenCode 需要 auth，必须走明确 auth 流程，不能假设本地已登录。
3. 创建 Task 时建立或记录 OpenCode Session。
4. 所有后续 assistant message、Final Prompt 生成、Tool Library 调用都使用同一个 `opencode_session_id`。
5. OpenCode 调用失败时，OpenCrew Task 不应丢失主状态。
6. OpenCode messages 可作为 UI 辅助读取，但不能作为 OpenCrew Task 恢复所需的唯一数据源。
7. Provider / model 列表可缓存，但运行时选择必须保存到 Task / Attempt。

### 12.4 Workspace 接入详细流程

Workspace 接入必须满足：

1. workspace 根目录由后端统一生成。
2. workspace 路径写入 `sessions.workspace_dir`。
3. 用户上传文件写入 workspace 下允许目录。
4. 工具输出文件写入 workspace 下允许目录。
5. 每次输出后同步 `session_files`。
6. 前端只使用相对路径请求 raw / zip。
7. 删除 Task / Session 时必须根据删除策略清理 workspace。
8. workspace 中的文件不能反向作为 Task 主状态来源。

### 12.5 状态恢复流程

页面刷新或服务重启后，必须按以下顺序恢复：

1. 从 task API 读取 Task 主状态。
2. 从 sessions 读取 Session 状态、workspace、OpenCode Session 引用。
3. 从 version API 读取版本列表和当前版本。
4. 从 attempts 读取最新运行记录。
5. 从 session events 读取历史事件。
6. 从 session files 读取文件索引，必要时扫描 workspace 补齐。
7. 从 OpenCode 读取 live messages 或继续对话上下文。

不得依赖：

- 前端 React state。
- Debug Console local store。
- workspace 中某个业务文件作为唯一 task 状态。
- OpenCode 最近一条 message 作为唯一 Final Prompt。

## 13. 验收标准

### 13.1 数据库验收

- 新建 Task 后可查到 task row。
- 新建 Task 后可查到 session row。
- 如需要 OpenCode，可查到 `opencode_session_id`。
- Simple Prompt 可刷新恢复。
- Final Prompt 可刷新恢复。
- Final Prompt Version 可保存、切换、删除。
- Run 后生成 attempt。
- Attempt 状态可从 queued 到 running 到 completed / failed。

### 13.2 OpenCode 验收

- Task 创建时绑定 OpenCode Session。
- Simple Prompt -> Final Prompt 使用同一个 OpenCode Session。
- 后续持续对话使用同一个 OpenCode Session。
- 切换 Final Prompt Version 不新建 OpenCode Session。
- OpenCode messages 不复制为数据库主状态。

### 13.3 Session Detail 验收

- 可通过 `#/sessions/task/{session_id}` 打开。
- 展示最终状态。
- 展示用户和 Assistant 消息。
- 展示可读工作日志。
- 展示文件列表。
- 可下载单文件。
- 可下载 ZIP。
- 不展示 debug-only payload。
- 不展示 secrets。
- public 事件可见，debug 事件不污染客户页。

### 13.4 Debug Console 验收

- 可按 session 激活。
- 可加载历史事件。
- 可连接 live stream。
- 模型调用有 requested / calling / completed / failed。
- 工具调用有 started / heartbeat / completed / failed。
- 长任务有 heartbeat。
- 错误有 family、severity、detail。
- payload 被截断和 sanitize。
- 刷新后关键 debug 历史仍可加载。

### 13.5 文件交付验收

- workspace 文件可索引。
- 文件路径不逃逸 workspace。
- 文件可下载。
- ZIP 可下载。
- 上传文件写入 workspace。
- 隐藏文件和敏感文件不进入客户交付列表。

### 13.6 WorkflowAssistant 验收

- bootstrap 能返回 task、session、plan 和模型选择。
- 发送 assistant message 使用同一个 OpenCode Session。
- plan 生成后写入 `workflow_plans`。
- plan confirm 后刷新页面仍为 confirmed。
- 高成本步骤未确认前不能直接执行。
- plan confirm 写入 session event。
- 运行工具链时 Debug Console 有工具事件。

### 13.7 版本与恢复验收

- 保存多个版本后刷新页面仍可看到版本列表。
- 切换版本后当前 Simple Prompt / Final Prompt 正确恢复。
- 删除非当前版本不影响当前运行。
- 删除当前版本触发明确 fallback。
- 历史 attempt 不因版本删除而丢失运行摘要。
- 服务重启后 Task detail 可恢复。
- OpenCode 不可用时仍能展示 Task、版本、attempt、文件和历史事件。

### 13.8 删除与清理验收

- 删除 Task 后 Task list 不再显示该任务，或显示为已删除状态。
- 删除 Task 后 Session Detail 不出现半残页面。
- 删除 Task 后 `session_events` 不留下无主记录，或可通过软删除策略解释。
- 删除 Task 后 `session_files` 不留下无主记录。
- 删除 Task 后 workspace 按策略清理。
- 删除失败时返回明确错误，不做部分静默失败。

### 13.9 安全验收

- raw file API 不能读取 workspace 外文件。
- zip API 不能打包 workspace 外目录。
- upload API 不能覆盖敏感路径。
- Session Detail 不显示 secrets。
- Debug Console payload 已 sanitize。
- share token 无法访问 Debug Console。
- public event 不包含内部 auth、provider token、request headers。

## 14. 优化建议

### 14.1 抽象 Workflow 基础表

建议新增通用 `workflow_tasks`、`workflow_versions`、`workflow_attempts`，业务表只保留扩展字段。

收益：

- Task List 统一。
- Attempt 统一。
- WorkflowAssistant adapter 更少硬编码。
- Session Detail 和 Debug Console 可更稳定复用。

### 14.2 抽象 Workflow Event Helper

建议新增后端 helper：

- `emit_public_event`
- `emit_debug_event`
- `emit_model_event`
- `emit_tool_event`
- `emit_attempt_event`
- `emit_error_event`

自动补齐：

- `workflow_id`
- `task_id`
- `session_id`
- `attempt_id`
- `visibility`
- `event_scope`
- `severity`
- `created_at`

### 14.3 Session Detail Presenter 与 Debug Presenter 分离

建议前端独立：

- `SessionDetailPage`
- `SessionMessageList`
- `SessionFileList`
- `SessionPublicLog`
- `DebugConsole`
- `debugStore`
- `debugAdapter`

不要在一个组件中混合客户状态页和调试控制台逻辑。

### 14.4 Provider Cache 只做降级

OpenCode provider/model 是 live 数据，不作为业务真相。

可以缓存最近一次成功结果作为 UI 降级，但必须标记 stale。

## 15. 实现坑点

### 15.1 Session Detail 与 Debug Console 混用

风险：客户页面看到内部 debug payload，或者 Debug Console 缺少必要调用链。

规避：使用 `visibility` 和 `event_scope`。

### 15.2 OpenCode Session 被重复创建

风险：持续对话上下文断裂。

规避：一个 Task 默认只绑定一个 OpenCode Session。

### 15.3 Final Prompt 只存在 OpenCode 消息里

风险：刷新或 OpenCode 不可用时 Task 不可恢复。

规避：Final Prompt 和版本必须入库。

### 15.4 Debug 事件只存在前端本地

风险：刷新后无法排查长任务。

规避：关键事件必须写 `session_events`。

### 15.5 文件路径逃逸 workspace

风险：泄露本地文件或无法下载。

规避：raw file 和 zip API 必须做 safe join。

### 15.6 大 payload 污染事件流

风险：Debug Console 卡顿、泄露敏感数据。

规避：后端和前端都必须截断 base64、bytes、blob、长文本。

### 15.7 PostgreSQL 实例和环境错连

风险：后端连接到错误的 PostgreSQL 实例，表现为表不存在、数据消失、前端列表为空或旧数据混入。

规避：

- 启动前确认 `DATABASE_URL`。
- 后端日志打印数据库 host、port、database，但不打印密码。
- bootstrap 只对当前目标数据库执行。
- 本地开发文档中明确推荐启动命令和端口。

### 15.8 bootstrap 补列不完整

风险：旧数据库缺少新字段，代码在读取 `opencode_session_id`、`workspace_dir`、版本指针或模型字段时失败。

规避：

- 每个新增字段都在 bootstrap / migration 中补列。
- repository 层读取字段时保留明确错误信息。
- 不用吞异常的方式掩盖 schema drift。

### 15.9 OpenCode auth / endpoint 未就绪

风险：Task 创建成功但无法生成 Final Prompt，或者 WorkflowAssistant message 失败。

规避：

- 启动时检查 OpenCode endpoint。
- UI 明确展示 OpenCode 未连接。
- OpenCode 不可用时仍允许查看 Task、版本、历史事件和文件。
- 失败事件写入 `session_events`，Debug Console 可见。

### 15.10 `opencode_session_id` 丢失

风险：后续对话创建新上下文，Final Prompt 生成和运行不连续。

规避：

- Task create 后立即保存 `opencode_session_id`。
- 所有 API 从 task / session 读取该值，不从前端临时传值作为唯一来源。
- 如果缺失，必须返回可诊断错误或执行明确 repair 流程。

### 15.11 Final Prompt 版本与 Task 当前字段不一致

风险：UI 显示版本 A，Run 实际使用版本 B，结果无法追溯。

规避：

- Run 前以后端 task 当前 version 指针为准。
- Run 创建 attempt 时记录 `prompt_version_id` 和 `runtime_version_id`。
- Version load 必须同步 task 当前字段和 current version 指针。

### 15.12 前端本地 Debug 事件未落库

风险：刷新页面后 Debug Console 没有长任务失败原因，AI 无法继续排查。

规避：

- 前端 local debug event 只做即时 UI 增强。
- 模型调用、工具调用、attempt 状态、错误必须由后端写 `session_events`。
- Debug Console 加载时先读历史事件，再连接 SSE。

### 15.13 workspace stale files 与 attempt mismatch

风险：Session Detail 展示上一轮运行文件，用户下载到旧结果。

规避：

- 文件索引应记录 origin / kind / attempt 信息。
- Run 开始时可以标记旧文件为历史文件。
- Session Detail 明确展示最新 attempt 文件或提供历史切换。

### 15.14 文件路径 URL 编码和空格问题

风险：文件名含空格、中文、特殊字符时 raw / zip 下载失败。

规避：

- 前端请求路径必须 encode。
- 后端必须 decode 后 safe join。
- 文件列表返回原始相对路径和可显示名称。

### 15.15 Session Detail 被 Debug 噪音污染

风险：客户看到 `provider`、`request_id`、工具参数、内部错误堆栈。

规避：

- Session Detail 只展示 public + session_detail / both。
- Debug event 即使写入同一表，也不得进入客户 presenter。
- public error 只写人类可读摘要，debug error 写完整可排查细节。

### 15.16 WorkflowAssistant plan 只存在前端状态

风险：刷新后 plan 消失，确认状态丢失，高成本步骤可能被重复确认或绕过。

规避：

- plan 必须写 `workflow_plans`。
- confirm 必须更新数据库。
- plan 变更和确认写入 `session_events`。

### 15.17 Run / Rerun 覆盖历史 attempt

风险：无法追溯上一次失败原因，也无法解释当前文件来自哪次运行。

规避：

- 每次 Run / Rerun 创建新 attempt。
- task 只保存 latest attempt 指针。
- 历史 attempt 不应被覆盖。

### 15.18 前端 build 目录混淆

风险：修改了子模块前端但启动的是另一个 build 输出，导致以为改动无效。

规避：

- 明确每个前端包的 build 输出和宿主加载方式。
- 修改工作流前端后运行对应 package 的 build。
- 验证浏览器实际加载的 bundle 来源。

### 15.19 provider/model cache 过期

风险：UI 显示的模型不可用，Run 时失败。

规避：

- provider cache 标记 stale。
- Run 前以后端实际 provider/model 校验为准。
- 失败写入 debug event 并给客户页可读错误。

### 15.20 业务产物细节污染基础设施

风险：基础设施 PRD 被某个 Workflow 绑死，后续工作流无法复用。

规避：

- 本 PRD 只定义 Task / Session / Version / Attempt / Event / File 通用标准。
- 具体业务产物、文件 schema、目录约定、运行步骤放入业务 PRD。
- 本 PRD 可以列业务模块作为样例，但不定义它们的业务细节。

## 16. 参考实现文件

Session Detail 当前参考：

- `frontend/src/App.jsx`
- `backend/opcrew_backend/routes/sessions.py`
- `backend/opcrew_backend/repositories/sessions.py`

Debug Console 当前参考：

- `frontend/src/debug/DebugConsole.jsx`
- `frontend/src/debug/debugStore.js`
- `frontend/src/debug/debugAdapter.js`

OpenCode 当前参考：

- `backend/opcrew_backend/adapters/opencode.py`

WorkflowAssistant 当前参考：

- `WorkflowAssistant/backend/workflow_assistant/routes.py`
- `WorkflowAssistant/frontend/src/WorkflowAssistantDrawer.jsx`

数据库当前参考：

- `backend/opcrew_backend/db/schema.py`
- `backend/opcrew_backend/db/bootstrap.py`

## 17. 最终结论

OpenCrew 后续所有工作流都必须遵守以下边界：

```text
数据库：工作流主状态、Prompt、Version、Attempt、Session、事件索引
OpenCode：模型、Agent、持续对话、实时 messages
Workspace：文件、交付物、工具输入输出
Session Detail：客户状态页、消息记录、文件和交付物下载
Debug Console：系统调试页、长任务状态、模型/工具调用链、错误排查
```

该 PRD 是未来所有 Workflow 的基础设施标准。具体业务产物、业务文件 schema、业务运行步骤必须在各自业务 PRD 中定义，不应污染本基础设施 PRD。

## 18. 非规范附录：外部架构 Review 与采纳映射

本章是外部架构 review 建议的归档，不属于本 PRD 的规范性验收标准。执行优先级、阶段安排和取舍理由以 `docs/opencrew_repo_improvement_plan.md` 为单一真相来源。

采纳映射：

| 建议 | 状态 | 计划归属 / 取舍 |
| --- | --- | --- |
| 18.1 异步事件总线 | 推迟 | P0 先收敛 `SessionEventService`、visibility、replay 合同；事件量和阻塞问题可在合同稳定后作为性能优化评估。 |
| 18.2 计划三态机 / 两阶段确认 | 部分采纳 | P3.1 / P3.2 采纳持久 run/step 状态、suspend/resume、幂等键；严格两阶段提交和版本冻结作为 P3 设计输入，不进入 P0。 |
| 18.3 Workspace 安全沙箱 | 部分采纳 | canonical path / symlink 防逃逸进入 P0.3；受限 OS 用户沙箱推迟到生产部署安全加固。 |
| 18.4 `seq_id` / 断线重放 | 已采纳 | 进入 plan P1.5；也允许用全局 `session_events.id` 作为 durable cursor，只要能证明分页补齐不丢事件。 |
| 18.5 Prompt Registry 热加载 | 推迟 | 当前优先收敛 DB 主状态、Version、Attempt；Prompt 模板热加载属于未来可维护性优化。 |
| 18.6 OpenCode auto-healing | 推迟 | 当前先要求可诊断失败和主状态不丢失；自动重建 OpenCode Session 可能隐式改变上下文，需另行设计。 |
| 18.7 PII 脱敏与 token/cost | 已采纳 | P0.2 采纳 PII/secret redaction；P1.3 采纳 attempt metrics 字段。 |

以下保留原始建议内容，仅作 future backlog 和设计参考。

### 18.1 建立统一事件总线（Event Bus）与异步解耦机制

*   **现状评估**：当前 PRD 描述的事件落库和实时推送（SSE）主要在主执行链路中同步触发。这在面对大量、快速的 Debug 级或工具级事件时，会因为同步数据库 I/O 导致主执行协程卡顿，增加长任务调用的系统开销。
*   **优化建议**：
    1.  在 Python 后端引入基于异步队列（如 `asyncio.Queue`）的轻量级事件总线机制。
    2.  事件生成方只需非阻塞地向事件总线 `emit_event`，由独立消费协程（Event Worker）负责分发，分别写入 PostgreSQL 数据库日志以及实时 SSE 的推送通道。
    3.  通过总线隔离，确保单次 DB 写入慢不影响 SSE 实时传输，也不影响主执行链路。

### 18.2 两阶段确认协议与计划完整状态机

*   **现状评估**：`WorkflowAssistant` 的计划生成与执行（`workflow_plans`）属于高成本和多步骤交互行为。若在规划中途遭遇网络丢包或前端断连，容易产生执行计划状态不一致的情况。
*   **优化建议**：
    1.  为 `workflow_plans` 引入严格的三态状态机：`Draft`（草稿状态，由 Agent/模型推荐生成）、`Proposed`（等待确认，锁定关联参数）、`Committed`（已确认/已提交，代表用户最终授权）。
    2.  要求 `confirmed_by_message_id` 和关联的版本指针（`version_id`）具备强相关性，确认执行计划的同时自动冻结当时的版本快照，实现两阶段提交协议。

### 18.3 Workspace 安全沙箱与文件访问限域隔离

*   **现状评估**：在文件存储与交付中，虽然限制了相对路径，但若直接进行路径拼接（`safe_join`），仍无法彻底杜绝由软链接（Symlink）、本地恶意可执行工具篡改等引起的安全逃逸风险。
*   **优化建议**：
    1.  对所有文件读写和 ZIP 压缩接口，后端不仅要做 `safe_join` 校验，还应强制执行物理根目录（Canonicalized Absolute Path）的二次比对（`os.path.realpath`）。
    2.  工具执行层（Tool Library）调用外部命令时，应对 workspace 运行路径实施受限用户（Restricted User）权限隔离，避免工具在越权状态下执行文件破坏或读取宿主机系统文件。

### 18.4 流式事件状态序列号（seq_id）与断连自动纠错

*   **现状评估**：SSE 事件流（`GET /api/sessions/{session_id}/events/stream`）由于 HTTP 长连接特性，极易在移动网络下因为断线产生数据空隙。前端如果不做序列管理，重连后会导致 UI 数据丢失或长任务日志不全。
*   **优化建议**：
    1.  每个 `session_events` 数据库记录和 SSE 推送帧必须包含自增的全局或 Session 域唯一的序列号 `seq_id`。
    2.  前端重连时，在 SSE URL 中携带参数 `last_seq_id=X`。
    3.  后端 SSE 接口检测到 `last_seq_id` 时，首先从数据库中查询并重放 `X` 之后的未推送事件，再开始正常订阅新事件，确保前端断线重连依然能无缝恢复事件流。

### 18.5 Prompt 模板从代码热插拔解耦

*   **现状评估**：当前 Simple Prompt 和模板拼接主要在后端代码或路由中直接组装。一旦业务逻辑微调或需要进行 Prompt 快速 AB 测试时，需要频繁部署代码。
*   **优化建议**：
    1.  建议建立独立的 Prompt Registry，将工作流的 Prompt 模板（包含元数据、模型绑定）独立为 JSON 或 YAML 静态资产文件，存放到特定目录中并支持配置热加载。
    2.  后端运行时使用 Jinja2 或等价引擎动态加载并组装 Prompt。版本保存时，不仅要把结果落库，还要保留当时所载入的模板文件名与 hash。

### 18.6 OpenCode Session 探针检测与弹性容错（Auto-Healing）

*   **现状评估**：OpenCode Session 可能因服务重启、Token 过期或 CLI Server 宕机而发生断连，直接导致依赖同一 `opencode_session_id` 的后续持续对话失败。
*   **优化建议**：
    1.  在执行 `Run` 动作或发送助理消息前，引入轻量级的 Session 探针检测机制（Ping-Pong / Get Session Status）。
    2.  如果发现 `opencode_session_id` 已在 OpenCode 侧失效，但本地 Task/Session 仍为活跃状态，应自动执行弹性修复（Auto-Healing）：重新请求创建 OpenCode Session，通过恢复上一版本的 Prompt Version 隐式补充上下文，确保主执行流程不因 OpenCode 服务短暂瞬断而发生崩溃。

### 18.7 PII 隐私脱敏、Token 追踪与审计日志

*   **现状评估**：在 Session Detail 进行客户交付展示时，部分用户隐私数据（如手机号、路径、密钥等 PII 敏感信息）可能会随着 SSE 或 Debug Payload 泄露给分享页访客。
*   **优化建议**：
    1.  在 `emit_public_event` 辅助函数中内嵌通用隐私脱敏过滤器（Sanitizer），自动脱敏符合 PII（Personally Identifiable Information）规则的数据。
    2.  在 `workflow_attempts` 数据库模型中增加 `input_tokens`、`output_tokens`、`estimated_cost` 字段，通过 Tool Library 调用和模型调用的反馈事件收集耗用情况，从而在 Debug Console 或 Session Detail 中呈现准确的运行额度和成本度量。
