# Agent 工作流管理实现规范：规划要点索引

版本：v0.1  
状态：规划索引，不是正式实现规范  
盘点日期：2026-07-24  
盘点范围：当前 OpenCrew 仓库内的需求、设计、评审、操作手册、Tool Registry、关键实现与契约测试

## 0. 本文件的用途

本轮不编写正式规范，只完成以下工作：

1. 规划后续需要编写的规范文档数量、边界和依赖关系。
2. 从现有需求与代码中提取值得继承的设计。
3. 标出需求之间、需求与代码之间尚未统一的口径。
4. 建立后续写规范时使用的参考文件清单和证据优先级。

本文件不应被当作最终 API、数据库 Schema、状态枚举或目录合同。所有“建议”“待决”内容必须在对应正式规范中定稿。

## 1. 本地材料盘点结论

### 1.1 扫描规模

本次扫描到：

- 266 个可直接阅读的文档类文件：
  - 243 个 Markdown。
  - 18 个 HTML。
  - 2 个 Word 文档。
  - 3 个文本文件。
- 2 个 Plan Board 压缩归档：
  - `docs/PlanBoard.zip`
  - `docs/PlanBoard_win.zip`
- 按文件名包含“需求、设计、PRD、规范、标准、计划、评审、验收、Runbook”等关键词初筛，得到约 183 个候选文件。
- Markdown 内容哈希发现 24 组完全重复文件，共涉及 48 个文件。

归档判断：

- `docs/PlanBoard_win.zip` 中的 5 个文件均已在 `docs/PlanBoard/` 展开。
- `docs/PlanBoard.zip` 是较早的 macOS 打包快照，并包含 `__MACOSX` 元数据。
- 后续引用以展开目录为准，不从压缩包维护第二份标准。

### 1.2 与本规范最相关的材料区域

| 区域 | 主要价值 | 本轮判断 |
| --- | --- | --- |
| `docs/工具调用会话管理*.md` | Tool Use Session、工具目录、状态、断点、Registry、DB 写回 | 核心规范来源 |
| `docs/opencrew_task_process_indicator_mvp_design.md` | Attempt、串行 Runner、暂停/继续、单步、参数与日志 UI | 核心规范来源 |
| `docs/PlanBoard/` | Control-M 映射、Plan Row、Job、条件、五色状态、文件绑定 | 核心规范来源 |
| `docs/opencrew_workflow_data_storage_*.md` | Task/Session/Attempt、DB 与 Workspace 边界、事件和文件安全 | 核心架构来源 |
| `docs/SessionDesign-R2/` | 工具输入输出、StoryBoard 状态、槽位绑定、实际踩坑 | 领域验证与边界案例 |
| `docs/DanceMimic_V1/` | 新工作流接入、独立 00、薄封装、状态分层 | 新工作流接入样例 |
| `ToolLibrary/*/tool_registry.json` | 现有 6 个工具集、108 个工具的真实命名和能力字段 | 现状证据 |
| `backend/opcrew_backend/tool_sessions/` | 通用合同 Schema、路径、依赖、Runner、同步 | 可复用基础实现 |
| `backend/opcrew_backend/koubo/router.py` | Analysis_V1 已运行的 Attempt/暂停/继续/日志实现 | 可抽象参考实现 |
| `backend/opcrew_backend/koubo/koubo_storyboard/` | 五色状态、稳定 key、签名和文件绑定 | Plan Board 派生层参考 |
| `backend/tests/contracts/` | 已落地合同的回归证据 | 验收基线 |

### 1.3 总体判断

仓库不是从零开始，已经有四块可复用基础：

1. 通用 Tool Use Session 的 Schema、目录、Manifest、依赖检查、heartbeat、idempotency 和结果同步。
2. Analysis_V1 的线性 Task Process Runner，以及暂停点、继续、停止、单步、重跑、参数快照、日志和页面恢复。
3. OpenCrew Session 的持久事件、SSE 增量读取、文件可见性和 workspace 路径安全。
4. StoryBoard 的五色状态纯函数、稳定业务 key、输入签名和“文件优先于旧运行态”的派生规则。

仍缺少三块统一能力：

1. Tool Registry、Task Process、Analysis_V1 专用 Runner 之间还没有收敛成一套通用执行内核。
2. Plan Board 的通用 Schema、per-stage Running/Failed 标记、统一 API 和跨浏览器状态协议尚未正式实现。
3. 自动重试、失败后是否继续执行、工具内部 checkpoint、幂等键语义、并发写入和状态词汇仍需统一定稿。

## 2. 建议文档套件

建议最终形成 **9 份文件：1 份索引 + 8 份正式规范**。

| 编号 | 文档 | 核心内容 | 依赖 |
| --- | --- | --- | --- |
| 00 | 本规划要点索引 | 范围、文档拆分、参考清单、冲突与待决项 | 无 |
| 01 | 总体架构与统一术语规范 | Workflow、Task、Session、OpenCode Session、Attempt、Tool Use Session、Plan、Plan Row、Job 的关系与权威边界 | 00 |
| 02 | Tool Library 与 Tool Contract 规范 | 工具集、工具 ID/名称/入口、Registry Schema、能力声明、prepare/run/finalize、ToolResult、Manifest | 01 |
| 03 | Task Process Session 与数据合同规范 | Workspace、工作目录、DB 访问、Session Variables、Context、Input/Output、事件、文件索引、GC | 01、02 |
| 04 | Task Process Runner 可靠性规范 | 状态机、失败分类、超时、heartbeat、自动重试、幂等、checkpoint、resume、force rerun、继续策略、并发锁 | 02、03 |
| 05 | Task Process 运行控制界面规范 | 运行设置、进度、暂停点、继续、停止、单步、断点、变量/日志/文件查看、刷新恢复、权限与操作禁用 | 04 |
| 06 | Plan Board 数据模型与状态派生规范 | Plan/Plan Row/Job、Wait for Events、Actions、文件绑定、稳定 anchor、签名、五色状态、资源与锁预留 | 01、03、04 |
| 07 | 持久化、实时同步与安全规范 | DB 权威状态、快照与事件游标、SSE/轮询、多浏览器一致性、并发控制、脱敏、文件访问、审计 | 03、04、05、06 |
| 08 | 迁移、测试与验收规范 | 兼容层、Registry 迁移、旧目录迁移、灰度、回滚、契约测试、故障注入、性能与多浏览器验收 | 01–07 |

推荐编写顺序：

```text
01
├─ 02 ─ 03 ─ 04 ─ 05
└────────────── 06
          03/04/05/06 ─ 07
                    01–07 ─ 08
```

不建议把全部内容合并成一篇大文档。Tool Contract、Session/Data Contract、Runner 状态机和 Plan Board 状态派生的变更频率不同，拆分后更适合代码评审、版本化和自动测试引用。

## 3. 规划要点索引

### 3.1 Tool Library 与单工具实现

#### 3.1.1 值得继承

1. Registry 是唯一机器可读工具清单，Runner 不从自然语言说明猜测依赖。
2. `schema_version` 使用字符串语义版本，例如 `"1.0"`。
3. 每个工具声明：
   - `tool_id`、`tool_name`、`script`、`stage`。
   - `reads_session_context`、`writes_session_context`。
   - `consumes_outputs`、`produces_outputs`。
   - runtime、Python package、data asset、provider 等依赖。
   - `supports_resume`、`supports_progress_log`、timeout/heartbeat 能力。
   - 成本等级、是否需要人工确认、是否使用模型。
4. 单工具采用 `prepare -> run -> finalize`：
   - prepare 校验依赖并把上游产物快照到本工具 Working。
   - run 只读本工具 Working、Prompt 和允许的 Session Context。
   - finalize 发布 Output、Report、Context Patch 和 Manifest。
5. 每个工具目录包含 `State.json`、`Working/`、`Output/`、`Report/`、`Prompt/`。
6. `Output/OutputManifest.json` 是下游消费入口，不允许扫描目录猜测产物。
7. Prompt 模板、变量、渲染结果和模型调用审计均可追溯，真实 secret 不落盘。
8. 工具只提交 Session Context patch，由 Runner 按 ownership 校验后合并。
9. workspace 路径使用 realpath 边界校验，拒绝绝对路径、`..` 和 symlink 逃逸。

#### 3.1.2 正式规范必须补齐

1. 工具集 ID、工具 ID、工具名、脚本名、step instance ID 必须分层，不能再混用。
2. 建议分别定义：
   - `toolset_id`：稳定小写 ASCII，例如 `analysis_v1`。
   - `tool_id`：工具集内稳定 ID，不包含业务对象实例。
   - `tool_name`：稳定 ASCII 机器名。
   - `display_name`：可本地化 UI 名称。
   - `step_instance_id`：某次 Plan/Attempt 中的实例 ID。
3. 必须确定数字编号规则是否保留，以及 `02_0`、`04_1`、`04_01_free`、长语义 ID 如何迁移。
4. Registry 中 repo-relative `script` 路径只能有一种基准，不允许同时出现 `OpenCrew/ToolLibrary/...` 和 `ToolLibrary/...`。
5. ToolResult 的 terminal status 应与 Runner lifecycle status 分离。
6. 需要统一标准错误结构：
   - `error_code`
   - `category`
   - `retryable`
   - `resume_supported`
   - `user_action_required`
   - `suggested_action`
   - `safe_message`
   - `debug_ref`
7. 需要定义 warning、partial success、blocked、failed、cancelled 的下游消费规则。
8. 需要定义模型、HTTP、数据库、进程、文件、Schema、人工输入等错误的分类表。

#### 3.1.3 失败、重试与继续运行

正式规范至少要区分四种机制：

1. **同一次外部调用的传输重试**：网络超时、限流、可恢复 5xx。
2. **同一工具 step 的执行重试**：工具失败后再次执行。
3. **同一 Attempt 的 checkpoint resume**：从工具内部已完成的批次继续。
4. **新 Attempt 的 rerun**：从某步、失败步或全链重新运行。

需要定稿的关键字段：

```text
operation_id
idempotency_key
step_attempt_no
retry_no
checkpoint_id
previous_attempt_id
reuse_source_attempt_id
```

当前实现把 `retry_count` 同时用于 force rerun 和 idempotency key 变化，尚不足以表达“同一逻辑调用去重”与“用户明确要求重新计费执行”的区别。

正式规范还必须定义：

- `max_attempts`、指数退避、jitter、Retry-After、总时间预算。
- 哪些错误绝不自动重试。
- 高成本模型调用在已有成功审计时是否允许自动重复。
- checkpoint 文件的 Schema、完成边界、校验和和清理规则。
- `fail_fast`、`continue_independent`、`continue_on_warning`、`allow_partial` 等链路策略。
- 线性链路遇到 failed/blocked 默认停止；只有经过依赖分析确认不受影响的独立 Job 才允许继续。
- 用户修复变量或文件后，继续前必须重新执行依赖自检。
- stale heartbeat 的接管、孤儿进程判定和重复执行防护。

### 3.2 Task Process Session 管理

#### 3.2.1 建议统一的对象关系

正式规范应固定以下关系，并明确每一层负责什么：

```text
Workflow Definition
  └─ Task
      ├─ OpenCrew Session
      ├─ OpenCode Session
      └─ Attempt N
          └─ Tool Use Session / Process Run
              └─ Step Instance N
```

建议原则：

1. Task 是业务任务身份。
2. OpenCrew Session 是客户状态、事件和文件的通用容器。
3. OpenCode Session 是模型对话上下文，不是工具执行状态。
4. 每次 Run/Rerun 创建新 Attempt。
5. Attempt 是一次运行和计费的边界。
6. Tool Use Session 是 Attempt 内工具链的执行目录与上下文边界；是否严格 1:1 必须在 01 号文档定稿。
7. Step Instance 是暂停、重试、日志、checkpoint 和状态展示的最小编排单元。

#### 3.2.2 工作目录与访问边界

需要统一定义：

```text
<workspace>/
  tool_use_sessions/<tool_use_session_id>/
    0_SessionContext/
    SessionReport/
    SessionOutput/
    S{step_index}_{tool_name}/
```

同时必须解决现有两套目录合同：

- 新 Tool Session 使用 `0_SessionContext/`，S0 从 0 编号。
- Analysis_V1、DanceMimic、Plan Board 需求大量使用 `SessionContext/`，并出现 `S1_00_*`。

正式规范必须选一个 canonical 目录，并明确另一名称仅作为迁移 alias；不能让工具长期同时读写两套目录。

每个工具进程还必须明确：

- `process_cwd`
- `workspace_dir`
- `tool_session_root`
- `tool_dir`
- `repo_root`
- runtime Python/venv
- 可继承环境变量白名单

当前通用 `SubprocessToolAdapter` 以 workspace 为 cwd，而 Analysis_V1 专用 Runner 以 repo root 为 cwd。规范需要消除这种隐式差异。

#### 3.2.3 数据库访问

建议目标边界：

1. DB 主状态由受信任后端、Runner 或第 0 步读取和写回。
2. 普通沙盒 Tool 不直接连接业务数据库。
3. Tool 通过 Variables、InputManifest、broker/resolver 和受控 API 获取非 secret 配置与能力。
4. provider key、DB URL、Authorization、cookie 不进入 Variables、Prompt、stdout、ToolResult 或文件 Manifest。
5. Attempt、Step、session_events、session_files、result index 由受信任层写回 DB。
6. DB 失败时进入明确的 `blocked` 或 `completed_with_sync_error`，不得启动服务、猜测新数据库或静默忽略。

现有 Analysis_V1 文档仍允许部分工具运行时从 DB 读取真实 API key；这与通用 Tool Session PRD 的 broker/resolver 边界冲突，必须在 03 号文档统一。

#### 3.2.4 Session Variables、Context 与输入输出

值得继承的合同：

- `Variables.json`：小型结构化全局快照。
- `InputManifest.json`：输入路径、来源、sha256、size、visibility、sensitivity。
- `SessionContextPatch`：工具声明的全局变量变更。
- `OutputManifest.json`：工具级下游产物。
- `SessionRunSummary.json` / `run_state.json`：运行摘要。
- `SessionOutput/manifests/result_index.json`：Attempt 最终结果入口。
- `session_files`：可展示、下载、归档文件索引。
- `session_events`：生命周期、控制动作、错误、恢复和审计。

正式规范需要补充：

1. JSON 原子写、fsync、文件锁或 compare-and-swap。
2. 每份快照的 `revision`、`updated_at`、`writer` 和 Schema migration。
3. 大对象不得直接进入 Variables，应使用文件引用。
4. 每个引用必须是 workspace-relative，并带 checksum。
5. DB latest attempt -> result index -> SessionOutput manifest -> legacy fallback 的唯一读取顺序。
6. history/retention/GC 必须先解除 DB 引用再清理文件。

### 3.3 Task Process Runner 与运行界面

#### 3.3.1 值得继承的运行模型

Analysis_V1 已实现并可作为 UI/控制协议参考：

- 全量运行、范围运行、从指定步骤开始。
- 单独运行某一步。
- 从失败步骤、指定步骤或全量重跑。
- 前序步骤显式标记 `reused`，记录来源 Attempt。
- 新运行创建新 Attempt；pause/resume 在同一 Attempt 内进行。
- 未来步骤开始前的步骤边界暂停。
- graceful stop：当前步骤结束后停止。
- stdout/stderr 持久日志和 tail。
- 参数、命令、环境、输入、输出、Prompt、result 的只读 quick watch。
- 页面刷新后从后端 run state 恢复。
- active run 冲突返回 409。
- 后端 capabilities 决定前端按钮是否可用。

#### 3.3.2 必须明确的“暂停、断点、继续”

界面和 API 必须区分：

| 概念 | 语义 | 是否中断当前进程 |
| --- | --- | --- |
| Pause Before Step | 下一 step 启动前暂停 | 否 |
| Stop After Current | 当前 step 完成后停止链路 | 否 |
| Terminate Current | 终止当前子进程 | 是，需工具声明安全 |
| Tool Checkpoint | 工具内部批次/子步骤保存点 | 不一定 |
| Resume Attempt | 同一 Attempt 从暂停点或 checkpoint 继续 | 否或按工具能力 |
| Rerun | 新建 Attempt，从选定边界重跑 | 新执行 |
| Diagnostic Single Step | 单步诊断，不一定更新最终 result index | 仅运行指定 step |

“断点运行”不能只用一个按钮概括。正式 UI 应分别展示工作流步骤边界、工具内部 checkpoint 和调试单步。

#### 3.3.3 界面最小结构

建议运行界面包含：

1. 运行设置：
   - 运行模式、start/end、单步、复用来源、模型和业务选项。
   - 计划预览、预计成本和高成本确认。
2. Attempt 概览：
   - Task/Session/Attempt、状态、当前 step、进度、耗时、heartbeat。
3. Step 列表：
   - pending/reused/running/completed/failed/blocked/skipped/cancelled/stale。
4. 命令栏：
   - 停止、继续、取消暂停点、刷新、重跑、打开结果。
5. Step 详情：
   - 概览、变量/参数、命令与环境、文件、Prompt、日志、错误和 checkpoint。
6. 连接状态：
   - 在线、重连、状态可能过期、最近服务端 revision/event cursor。

变量查看默认只读。修改变量应通过单独的受控 override/patch 操作，并写审计事件，不能在运行详情里直接编辑 `Variables.json`。

### 3.4 Plan Board

#### 3.4.1 核心对象

建议继承现有定义：

```text
Plan Board
  └─ Plan Row N
      └─ Job N
          ├─ Wait for Events
          ├─ Actions
          ├─ Resources / Locks / Priority
          └─ Derived Job Status
```

硬规则：

1. 一个 Job 只代表一个最小业务产物或明确动作。
2. Plan Row 使用稳定业务 anchor，不使用数组下标、前端行号或生成时间。
3. Plan Board JSON 只定义结构、条件和动作，不制造完成态。
4. Job Status 由后端状态服务派生，前端不自行扫描文件或计算颜色。
5. 工具 Output 只有发布到业务 `SessionOutput/<domain>/Working/` 并完成必要绑定后，才成为业务完成事实。

#### 3.4.2 五色状态

建议沿用：

| 业务状态 | 颜色 | 含义 |
| --- | --- | --- |
| Ended OK | 绿 | 标准业务产物有效，必要绑定完成或进入明确修复态 |
| Executing | 黄 | 当前签名 Job 正在运行或等待已提交的外部任务 |
| Ended Not OK | 红 | 当前签名 Job 失败，且标准产物尚未完成 |
| Waiting Ready | 白 | 前提条件已满足，等待运行 |
| Waiting Blocked / Skipped | 灰 | 前提条件不满足、等待资源/人工确认，或下游已完成导致无需运行 |

固定优先级：

```text
绿 > 黄 > 红 > 白 > 灰
```

需要特别保留：

- 标准业务文件已经存在时，旧 Running/Failed 不得覆盖绿色。
- 灰色必须返回机器可读 `reason` 和可读说明。
- 文件存在但业务绑定缺失时必须进入明确 repair state，不能由前端猜测。
- `tone`、`color`、`ui_tone` 当前口径不一致，正式规范应拆成稳定业务状态与独立设计 token。

#### 3.4.3 Wait for Events 与文件绑定

第一版条件类型可继承：

- `file_exists`
- `file_bound`
- `manual_confirmed`
- `marker_absent`
- 预留 `resource_available`
- 预留 `lock_available`
- 预留 `custom_predicate`

需要标准化的文件关系：

```text
Business artifact:
SessionOutput/<domain>/Working/{row_anchor}_{stage}.{ext}

Running marker:
SessionOutput/<domain>/Working/{row_anchor}_{stage}_Running_{signature12}_{marker_uid}.json

Failed marker:
SessionOutput/<domain>/Working/{row_anchor}_{stage}_Failed_{signature12}_{marker_uid}.json
```

`step_signature` 必须覆盖输入文件、Prompt、绑定、模型配置和输出目标。旧签名标记不能染当前 Job。

#### 3.4.4 跨浏览器一致性

跨浏览器不能依赖某个浏览器的 localStorage 或内存状态。建议在 07 号文档固定：

1. DB 中保存 Plan/Attempt 主状态、控制命令和事件。
2. Workspace 中保存可恢复快照、业务文件和签名标记。
3. 后端提供带 `revision` 和 `event_cursor` 的 Plan Board snapshot。
4. SSE 推送变化事件；断线后按 cursor 从 DB 补齐。
5. 轮询作为降级路径，并支持 ETag/If-None-Match 或 revision 短路。
6. 多 Tab/多用户控制命令带 command id/idempotency key。
7. 写操作使用乐观并发版本或服务端锁，冲突返回最新状态。
8. 浏览器只保存视图偏好，不保存权威 Job Status。

当前 Session Events 已能按数据库事件 ID 增量读取和 SSE 重放；Plan Board 尚缺自己的 snapshot/API/事件合同。

## 4. 可借鉴实现与缺口矩阵

| 能力 | 可借鉴文件 | 已有内容 | 不能直接照搬的缺口 |
| --- | --- | --- | --- |
| Tool Contract Schema | `backend/opcrew_backend/tool_sessions/schemas/models.py` | 严格 Pydantic、Variables、Manifest、State、ToolResult | status 仍是自由字符串；缺 revision、标准错误、checkpoint、retry policy |
| Tool Session 路径 | `backend/opcrew_backend/tool_sessions/paths.py` | 独立 `tool_use_sessions/<id>`、Context/Report/Output/Step | 与现有 `SessionContext`、S1_00 合同冲突 |
| 安全输入复制 | `backend/opcrew_backend/tool_sessions/io.py`、`prepare.py` | realpath 边界、checksum、InputManifest | JSON 写入非原子；DB/Attempt 绑定主要靠调用方 |
| Registry 归一化 | `registry_normalizer.py` | 旧依赖 token 分类、严格 unresolved 失败、能力映射 | 仅针对部分已知 token；命名与路径漂移尚未治理 |
| 通用 Tool Runner | `tool_sessions/runner.py` | 依赖检查、heartbeat、stale、context ownership、manifest 校验 | 使用阻塞 `subprocess.run`；无暂停、流日志、自动 retry、checkpoint 和通用计划循环 |
| 结果同步 | `tool_sessions/result_sync.py` | result index、session_files 重建、可见性策略 | 需统一 Attempt/step DB 主状态和原子发布 |
| Analysis_V1 Runner | `backend/opcrew_backend/koubo/router.py` | Popen、日志 tail、run_state、pause/resume/stop/rerun、Indicator API | 业务路由巨型且强耦合 Analysis_V1；run_state 写入并非全部原子；无自动 retry |
| Task Process UI | `frontend/src/modules/koubo/AnalysisV1/AnalysisV1Module.jsx`、`analysisV1Api.js` | 运行模式、暂停点、继续、轮询恢复、详情与日志 | 需拆为 workflow-neutral 组件和状态协议 |
| Session 事件 | `services/session_events.py`、`repositories/sessions.py`、`routes/sessions.py` | visibility/event_scope、DB id cursor、SSE、历史补读 | Plan Board 事件族和 snapshot revision 尚未定义 |
| Session 文件安全 | `services/session_files.py` | workspace 边界、敏感路径、downloadable、share 过滤 | 需要与 Tool Manifest 和 Plan Board artifact policy 完全统一 |
| 五色状态 | `koubo_storyboard/slot_state_services.py` | 纯函数、绿优先、reason、UI tone | 当前是 StoryBoard 固定槽位，需要抽象 Job condition evaluator |
| 稳定 anchor | `video_plan_artifact_services.py` | dialogue/segment asset key 和 Working 文件识别 | 多领域 anchor 生成规则仍需插件化 |
| 输入签名 | `video_plan_signature_services.py` | scope/parameter/structure/binding/reference/input 六维签名 | 当前签名是 Video Plan 级，尚未绑定到通用 per-stage marker |
| Plan Board | `docs/PlanBoard/*` | 对象、五色、条件、标记、生命周期、迁移计划 | 未发现通用 PlanBoard 后端/前端实现；仍处于设计阶段 |

## 5. 正式规范前必须统一的冲突

| # | 冲突/缺口 | 当前表现 | 需要在哪份文档定稿 |
| --- | --- | --- | --- |
| C01 | Session Context 名称 | `0_SessionContext` 与 `SessionContext` 并存 | 01、03、08 |
| C02 | 第 0 步编号 | `S0_00_*` 与 `S1_00_*` 并存 | 02、03 |
| C03 | Tool ID 规则 | 数字、子编号、后缀、长语义 ID 混用 | 02、08 |
| C04 | Script 路径基准 | `OpenCrew/ToolLibrary/...` 与 `ToolLibrary/...` 并存 | 02、08 |
| C05 | Registry 能力字段 | 6 个 registry 字段完整度不同 | 02、08 |
| C06 | Runner 双轨 | 通用 ToolSessionRunner 与 Analysis_V1 专用 Runner 并存 | 04、08 |
| C07 | DB/secret 访问 | 通用 PRD 要求 broker；部分工具文档允许运行时读 DB key | 03、07 |
| C08 | 状态词汇 | Tool local、ToolResult、Attempt、Step、Plan Board 各有不同集合 | 01、04、06 |
| C09 | `tone` 语义 | Plan Board 文档用颜色名；代码用 done/pending/disabled/running/failed | 05、06 |
| C10 | Retry 与幂等 | force rerun 递增 retry_count 并改变 idempotency key | 04 |
| C11 | 失败后继续 | 通用线性 Runner默认停止；领域工具存在 per-segment 继续 | 04 |
| C12 | Checkpoint | PRD 提出 resume，通用 checkpoint Schema 未定义 | 04 |
| C13 | JSON 并发写 | 部分模块原子 replace，部分直接 write_text | 03、04、07 |
| C14 | Workspace 与 DB 权威 | 需求强调 DB 主状态，但多处仍靠 run_state/业务 JSON恢复 | 01、03、07 |
| C15 | Plan Board 标记 | Running/Failed per-stage marker 仅设计，尚未落地 | 06、08 |
| C16 | 跨浏览器 | Session SSE 已有；Plan Board snapshot/revision/command 冲突协议缺失 | 07 |
| C17 | 文件完成与绑定 | 某些领域“文件存在即绿”，另一些要求文件+双 JSON 绑定 | 06 |
| C18 | Attempt 与 Tool Use Session | 设计倾向 1:1，但未来批次/子运行关系未定 | 01、03 |
| C19 | 自动重试计费 | 高成本调用重试、复用和用户 rerun 的计费边界未统一 | 04、07 |
| C20 | 状态扫描性能 | Plan Board 刷新可能扫描大量 Working 文件并重算签名 | 06、07、08 |

## 6. 参考文件清单

### 6.1 一级：直接用于正式规范

#### 总体架构、Session 与数据

- `ARCHITECTURE.md`
- `docs/opencrew_workflow_data_storage_implementation_prd.md`
- `docs/opencrew_workflow_data_storage_prd_review.md`
- `docs/工具调用会话管理设计PRD.md`
- `docs/工具调用会话管理设计PRD_review.md`
- `docs/工具调用会话管理开发计划.md`
- `docs/工具调用会话工具迁移指南.md`
- `docs/SessionDesign-R2/OpenCrew_独立工作流参数存储与00扩展规范.md`
- `docs/SessionDesign-R2/Analysis_V1_00_PrepareSessionVariables_工具实现Workbook.md`
- `docs/DanceMimic_V1/DanceMimic_V1_标准Session管理与工具实现规范.md`

#### Task Process Runner 与 UI

- `docs/opencrew_task_process_indicator_mvp_design.md`
- `docs/analysis_v1_enter_task_vs_run_design.md`
- `docs/DanceMimic_V1/DanceMimic_V1_一键成片自动化编排需求.md`
- `docs/DanceMimic_V1/DanceMimic_V1_实施收敛设计.md`

#### Plan Board

- `docs/PlanBoard/README.md`
- `docs/PlanBoard/01_ControlM_实现调研分析报告.md`
- `docs/PlanBoard/02_PlanBoard_项目工作规划_v0.2.md`
- `docs/PlanBoard/03_PlanBoard_最终需求实现细节.md`
- `docs/PlanBoard/04_ControlM_概念命名确认表.html`

其中：

- `03_PlanBoard_最终需求实现细节.md` 是第一版实现合同主来源。
- `02_PlanBoard_项目工作规划_v0.2.md` 比无版本后缀的 `02` 更完整，包含对现有代码的复用核验。
- `README.md` 仍只列旧 `02`，正式整理时应更新索引指向。

### 6.2 二级：Tool Library 现状与实现证据

#### Tool Registry 与使用说明

- `ToolLibrary/Analysis/tool_registry.json`
- `ToolLibrary/Analysis/AGENT_TOOL_GUIDE.md`
- `ToolLibrary/Analysis_V1/tool_registry.json`
- `ToolLibrary/Analysis_V1/AGENT_TOOL_GUIDE.md`
- `ToolLibrary/DanceMimic_V1/tool_registry.json`
- `ToolLibrary/DanceMimic_V1/README.md`
- `ToolLibrary/OpenCut_V1/tool_registry.json`
- `ToolLibrary/OpenCut_V1/README.md`
- `ToolLibrary/Rebuild_V1/tool_registry.json`
- `ToolLibrary/Rebuild_V1/README.md`
- `ToolLibrary/Rebuild_V1/PHASE_RUNBOOK.md`
- `ToolLibrary/Rebuild_V1/RUNBOOK_rebuild_v1_pitfalls.md`
- `ToolLibrary/TalkingHead_V1/tool_registry.json`
- `ToolLibrary/TalkingHead_V1/README.md`

现状统计：

- 6 个 registry，共 108 个工具。
- `Analysis` 有 3 个非统一数字 ID，21 个工具名均未带编号前缀，script 均带 `OpenCrew/` 前缀。
- `Analysis_V1` 有 `04_01_free` 特例，18 个条目未声明 `supports_progress_log`。
- `Rebuild_V1` 有大量“编号 + 粒度 + Plan + 动作”长 ID，52 个 script 均带 `OpenCrew/` 前缀。
- DanceMimic/OpenCut/TalkingHead 的命名较接近“编号 + 机器名”，但 Registry 能力字段仍不完整。

#### 通用后端

- `backend/opcrew_backend/tool_sessions/schemas/models.py`
- `backend/opcrew_backend/tool_sessions/paths.py`
- `backend/opcrew_backend/tool_sessions/io.py`
- `backend/opcrew_backend/tool_sessions/prepare.py`
- `backend/opcrew_backend/tool_sessions/registry_normalizer.py`
- `backend/opcrew_backend/tool_sessions/runner.py`
- `backend/opcrew_backend/tool_sessions/result_sync.py`
- `backend/opcrew_backend/tool_sessions/model_broker.py`
- `backend/opcrew_backend/tool_sessions/service.py`
- `backend/opcrew_backend/services/session_events.py`
- `backend/opcrew_backend/services/session_files.py`
- `backend/opcrew_backend/repositories/sessions.py`
- `backend/opcrew_backend/routes/sessions.py`
- `backend/opcrew_backend/db/schema.py`

#### Analysis_V1 运行控制

- `backend/opcrew_backend/koubo/router.py`
- `backend/opcrew_backend/koubo/schemas.py`
- `frontend/src/modules/koubo/AnalysisV1/analysisV1Api.js`
- `frontend/src/modules/koubo/AnalysisV1/AnalysisV1Module.jsx`

### 6.3 三级：Plan Board/业务状态抽象的领域证据

- `docs/SessionDesign-R2/STORYBOARD_OUTPUT_STRUCTURE.md`
- `docs/STORYBOARD_WORKING_EXECUTION_STATUS_MARKER_REQUIREMENTS.md`
- `docs/SessionDesign-R2/Koubo_Storyboard_Plan执行与页面绑定一致性审计.md`
- `docs/SessionDesign-R2/Koubo_槽位矩阵与SegmentTruth回归测试金标准.md`
- `docs/SessionDesign-R2/Koubo_Storyboard_DialogueKey_统一资源绑定需求与测试案例.md`
- `StoryBoardRegression/docs/requirements/Koubo_Storyboard_Raw_Final_状态管理需求文档.md`
- `docs/SessionDesign-R2/Analysis_V1_05_01_VideoGenerationPlan_工具需求整理.md`
- `docs/SessionDesign-R2/Analysis_V1_05_02_VideoPlanExecutor_工具需求整理.md`
- `docs/SessionDesign-R2/Analysis_V1_05_03_05_04_ImagePlan_工具需求整理.md`
- `docs/SessionDesign-R2/Analysis_V1_05_05_05_06_VideoOnlyPlan_工具需求整理.md`
- `docs/requirements/Analysis_V1_06_01_VideoPlanComposer_工具需求整理.md`
- `docs/DanceMimic_V1/DanceMimic_V1_工具目标与实现需求.md`
- `docs/DanceMimic_V1/DanceMimic_V1_01_ReferenceMediaDemux_工具实现需求.md`
- `docs/DanceMimic_V1/DanceMimic_V1_03_StoryBoardStandardTaskBuild_工具实现需求.md`

对应代码：

- `backend/opcrew_backend/koubo/koubo_storyboard/slot_state_services.py`
- `backend/opcrew_backend/koubo/koubo_storyboard/video_plan_signature_services.py`
- `backend/opcrew_backend/koubo/koubo_storyboard/video_plan_artifact_services.py`
- `backend/opcrew_backend/koubo/koubo_storyboard/video_plan_execution_state_services.py`
- `backend/opcrew_backend/koubo/koubo_storyboard/video_plan_load_services.py`
- `backend/opcrew_backend/koubo/koubo_storyboard/asset_history_services.py`
- `backend/opcrew_backend/koubo/koubo_storyboard/io_utils.py`

### 6.4 四级：契约测试与验收基线

- `backend/tests/contracts/test_tool_session_contract.py`
- `backend/tests/contracts/test_analysis_v1_task_process_indicator_mvp_contract.py`
- `backend/tests/contracts/test_analysis_v1_runner_executable_contract.py`
- `backend/tests/contracts/test_session_event_policy_contract.py`
- `backend/tests/contracts/test_session_file_policy_contract.py`
- `backend/tests/contracts/test_session_routes_contract.py`
- `backend/tests/contracts/test_koubo_storyboard_slot_state_contract.py`
- `backend/tests/contracts/test_koubo_storyboard_manual_asset_status_contract.py`
- `backend/tests/contracts/test_koubo_storyboard_video_execution_state_contract.py`
- `backend/tests/contracts/test_analysis_v1_video_plan_executor_resilience_contract.py`
- `backend/tests/contracts/test_dance_mimic_prepare_session_variables_contract.py`
- `backend/tests/contracts/test_dance_mimic_toolchain_contract.py`
- `StoryBoardRegression/README.md`
- `StoryBoardRegression/Koubo_槽位矩阵与SegmentTruth回归测试金标准.md`

### 6.5 历史、重复与低优先级材料

以下材料仍可用于追溯，但不应直接覆盖当前代码和较新收敛文档：

1. `docs/PlanBoard/02_PlanBoard_项目工作规划.md`
   - 被 `02_PlanBoard_项目工作规划_v0.2.md` 补充。
2. `docs/DanceMimic_V1/DanceMimic_V1_工具目标与实现需求.md`
   - 文件自身声明为早期草案，需以 v0.6 收敛设计覆盖冲突口径。
3. `docs/opencrew_workflow_data_storage_prd_review.md`
   - 是 2026-05-25 的历史实现评审；其中 visibility、SessionFileService 等部分问题目前代码已发生变化，应作为反模式与迁移背景，不直接视为当前事实。
4. `docs/工具调用会话管理设计PRD_review.md`
   - 评审对象的 Must-fix 多数已回写到新版 PRD，但迁移量、Registry token、DB/broker 边界等风险仍有效。
5. `StoryBoardRegression/docs/` 下多份 requirements/audits/matrices 与 `docs/SessionDesign-R2/` 或根 `docs/` 完全重复。
6. `docs/SessionDesign-R2/` 中复制的：
   - `opencrew_workflow_data_storage_implementation_prd.md`
   - `opencrew_workflow_data_storage_prd_review.md`
   - `opencrew_repo_improvement_plan.md`
   应以根 `docs/` 同名文件为引用入口。
7. `.docx` 用户手册、截图、验收 artifact、provider 参考提示词、部署手册、模型泄露专项设计不作为本套规范的主结构来源；仅在 07/08 号文档涉及安全、操作验收或供应商调用时按需引用。

## 7. 后续正式编写的 Gate

开始写 01–08 正式规范前，建议先完成以下确认：

1. 选择 canonical `SessionContext` 目录名和 step 编号方案。
2. 确认 Attempt 与 Tool Use Session 的基数关系。
3. 确认统一 Runner 是抽取 Analysis_V1 Runner，还是扩展 `tool_sessions/runner.py`。
4. 确认普通 Tool 禁止直连 DB/provider secret，统一经第 0 步和 broker/resolver。
5. 确认 Tool terminal status、Step lifecycle status、Attempt status、Plan Board Job Status 的四层状态词汇。
6. 确认自动 retry、checkpoint resume、用户 rerun 的幂等与计费边界。
7. 确认 Plan Board “文件存在但绑定缺失”是绿色修复态还是独立非绿色状态，并允许不同 Job 类型声明完成条件。
8. 确认 Plan Board 第一版是否继续限制为 Plan Row 内串行、无跨行自动调度。
9. 确认多浏览器同步采用“snapshot + DB event cursor + SSE + polling fallback”。
10. 确认 Registry 命名和路径迁移是否允许 alias，以及 alias 的移除版本。

完成这些 Gate 后，01–04 应先于 UI 和 Plan Board 细节定稿；否则界面和颜色标准会再次绑定到多套不一致状态源。

