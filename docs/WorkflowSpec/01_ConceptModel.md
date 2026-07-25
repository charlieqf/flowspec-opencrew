# 01 · 概念模型：术语与关系

> 本文只讲**概念**（领域无关）。每个概念给出：定义、职责、边界（不是什么）、与其他概念的关系、以及 OpenCrew 参考实现位置。可实现的字段与 Schema 见各专章与 [07 绑定](./07_ImplementationBinding.md)。

## 0. 全景图与一句话定义

```
Process@version（可复用定义）──冻结快照──┐
Tool Registry / AI Profile ──冻结快照──┼──▶ Run（一次有界 DAG 执行）
session + task（一次业务实例）──归属───┘      ├── Context（本 Run 一份）
  │ session 拥有 Workspace + Event Stream     └── Step 1..N（逻辑节点）
  │ task 保存业务字段与当前版本指针                 └── Step Attempt 0..N（技术调度）
  └── Run 0..N（补件/返工创建新 Run）                    ├── Tool/Model operation 0..N
                                                        ├── Artifact/Checkpoint 0..N
                                                        └── Usage/diagnostic facts 0..N
```

| 概念 | 一句话 |
|---|---|
| **Process** | 一条业务流程的**可复用定义**；每次 Run 展开的步骤依赖图是 DAG，完整业务生命周期可跨 Run 循环。 |
| **session** | 一次业务实例的**身份与文件载体**：拥有 Workspace、事件流、长期会话身份（一笔贷款、一个尽调标的、一条视频）。 |
| **task** | 与 session 配对的**业务记录**：创作输入、选型、版本指针（业务字段都在这）。session + task 合起来 = 一次业务实例。 |
| **Run** | 对某个 session/task 的**一次 DAG 编排执行**；补件/返工创建新 Run，并可关联被替代的 Run。 |
| **Step Attempt** | **技术调度尝试**（≠Run）：某个 Step 被适配器执行的第几次；一次 Step Attempt 内可包含同一外部操作的 transport retry。 |
| **Checkpoint** | Tool 在安全完成边界发布的**恢复产物**；后续 Step Attempt 可据它续跑，但它不是图节点、状态或另一层 Attempt。 |
| **Step** | Run 中的**逻辑调度节点**；一次调度产生 Step Attempt，一个 Attempt 可不调用外部能力、调用一次，或因 Agent/fanout 调用多次。 |
| **Tool** | 步骤背后**真正干活的可执行体**，用声明式契约描述、由适配器执行。 |
| **Model Invocation / Agent Execution** | AI Tool 内部的受限执行：单次模型收费操作，或包含多次模型/工具调用的有界 Agent 内循环。 |
| **UsageRecord / Cost / Budget** | 每个 billable operation 的 token/媒体用量、费用与预算约束；Step/Run/session 只聚合。 |
| **Variable / Context** | Run 范围内**强类型的共享状态**（键值），门控并参数化步骤。 |
| **State** | 单个 Step 或整个 Run 的**生命周期状态**（含"卡在什么上"）。 |
| **Workspace** | 一个 session 的**文件根目录**，步骤产物与共享文件都在此。 |
| **Artifact / OutputManifest** | 步骤产出的**文件**及其清单，是步骤间文件交接的契约。 |
| **Condition** | 命名的**依赖信号**：生产者发布、消费者等待，双方不直接引用彼此。 |
| **Resource** | 步骤运行的**前置资源**：`mutex`（互斥）/`semaphore`（并发数）/`rate_limit`（速率）三原语。 |
| **Event** | 步骤/运行的**状态变化广播**，用于可观测与 UI。 |

### 0.1 先分清四种关系

图里的箭头不能一律读成“父子目录”或“先后调用”。FlowSpec 对核心概念使用四种不同关系：

| 关系 | 含义 | 典型例子 |
|---|---|---|
| **定义 / 实例化** | 可复用定义被某次执行冻结引用；定义本身不是运行记录的父对象 | `Process@version → Run.definition_snapshot` |
| **业务配对** | 两个职责共同表达一个业务实例；可以物理分表，也可以合并存储 | `session 1 ⇄ 1 task` |
| **逻辑包含** | 上层是下层的生命周期、查询与聚合边界，不等于磁盘目录必须同构 | `business instance → Run → Step → Step Attempt` |
| **引用 / 证据关联** | 事实记录用稳定 ID 指向生产者或作用域，不因此成为其可变子状态 | `Artifact → producer Attempt`、`Event → run/step/attempt` |

### 0.2 权威层级、基数与包含关系

下面这张图是本规范的**权威逻辑关系图**。`[1]` 表示恰好一个，`[0..N]` 表示可以尚未产生，也可以有多个；它描述逻辑归属，不要求数据库表或物理目录逐层嵌套。

```text
定义平面（可复用；不属于某个业务实例）
Process@version [1] ──引用──▶ Tool Registry / AI Profile / Schemas
        └──────────────在创建 Run 时冻结为 definition_snapshot──────────────┐
                                                                              │
业务实例平面                                                                  ▼
Business Instance [1] = session [1] ⇄ task [1]                         Run [0..N]
                         语义 1:1 配对                                    │
  session ──拥有──▶ Workspace [1] + Event Stream [1]                      ├─ supersedes_run_id ─▶ 前一 Run [0..1]
  task    ──保存──▶ 业务字段、输入修订、当前版本/最近 Run 指针             ├─ Context [1]
                                                                            └─ Step [1..N]
                                                                                 │
执行平面                                                                         ├─ Human Task [0..N]（confirm/suspend 关联）
Step [1] ──调度──▶ Step Attempt [0..N]（skipped 时为 0；重调度时递增）            └─ Step Attempt [0..N]
                         │
                         ├─ Fanout Item Attempt [0..N]（每个 item Attempt 也至多含 1 个 Agent Execution）
                         ├─ Agent Execution [0..1]
                         │    ├─ Model Invocation [1..N]
                         │    └─ Tool Call [0..N]
                         ├─ 普通 Tool / 外部 operation [0..N]
                         ├─ canonical Artifact [0..N] + Checkpoint [0..N]
                         └─ Attempt diagnostic [0..N]

横切证据平面（通过 ID 关联，不是另一棵执行树）
Event ──引用──▶ session/task/run/step/attempt；UsageRecord ──引用──▶ billable operation
Artifact ──引用──▶ producer Step Attempt；Budget/Cost 在 operation 记事实、向 Step/Run/session 聚合
```

| 上层 / 关联源 | 下层 / 目标 | 基数 | 生命周期与身份规则 |
|---|---|---:|---|
| Process version | Run definition snapshot | `1 → 0..N` | 多个 Run 可冻结同一版本；Run 创建后不得悄悄跟随“最新 Process” |
| session | task | `1 ⇄ 1`（FlowSpec 业务实例内） | 是语义配对，不是“task 是 session 的文件子目录”；实现可以物理合并 |
| business instance | Run | `1 → 0..N` | 尚未执行可以为 0；补件、返工、重审新增 Run，旧 Run 不原地覆盖 |
| Run | Step | `1 → 1..N` | 每个 Process 逻辑节点在 Run 中有一条状态记录，包括最终 `skipped` 的节点 |
| Step | Step Attempt | `1 → 0..N` | guard 关闭时为 0；首次调度、技术重调度分别产生新的 Attempt |
| Step Attempt | transport retry | `1 → 0..N` | 网络级重试仍属于同一 Attempt 和同一 operation key，不新增 Step/Run |
| Step Attempt | operation / Model Invocation | `1 → 0..N` | Step 不等于一次调用；Model Tool 可有格式修复等多次 Invocation，纯计算或 guard 可为 0 |
| Step Attempt / Fanout Item Attempt | Agent Execution | `1 → 0..1` | Agent Tool 的一次技术尝试至多创建一个有界 Agent Execution；多个独立 Agent 必须拆成 Step 或 fanout item Attempt |
| Agent Execution | Model Invocation | `1 → 1..N` | Agent 至少经一次模型调用才建立执行身份；多轮调用共用同一 `agent_execution_id` |
| Agent Execution | Tool Call | `1 → 0..N` | 纯推理 Agent 可为 0；调用数量受 Profile allowlist 与 `max_tool_calls` 约束 |
| Step Attempt | Artifact / Checkpoint | `1 → 0..N` | Artifact 记录 producer `attempt_id`；Checkpoint 是恢复产物，不是新节点或成功状态 |
| Run | Context | `1 → 1` | Context 只在该 Run 内共享；新 Run 冻结自己的输入与 Context |
| billable operation | UsageRecord observation | `1 → 1..N` | 首次观测不可变；费用纠错通过 superseding observation，不覆盖旧记录 |
| 各身份层 | Event | `1 → 0..N` | Event 携带完整关联键形成时间线，但不替代各对象的物化状态 |

### 0.3 一次正常执行与一次业务返工的先后关系

```text
配对创建 session + task
  → 选择 Process/Tool/Profile 版本
  → 创建 Run，冻结 definition snapshot + input revision + Context
  → Runtime 按 DAG 找到 ready Step
  → dispatch Step，创建 Step Attempt
  → Attempt 内执行 0..N 个 operation / Model Invocation
  → finalize canonical Artifact，写 Usage/Event/诊断事实
  → 关闭不可达分支，满足且仅满足一个 Outcome，Run 终结
  → 若只是瞬态技术失败：同一 Run 内创建下一 Step Attempt
  → 若业务补件、人工返工或输入修订：创建下一 Run，并以 supersedes_run_id 连接旧 Run
```

必须避免五种常见误读：

1. `task` 与 `session` 是业务职责配对，不是两级重复文件树。
2. Run 是一次业务执行版本；Step Attempt 才是单步技术尝试。
3. Step 是依赖图中的逻辑节点，不等于 Tool、HTTP 请求或 Model Invocation。
4. Attempt 是调度/恢复边界，不自动等于一次收费；收费事实落在 operation/Invocation。
5. Event、Artifact、UsageRecord 是带关联键的证据，不应被塞回一个不断膨胀的“万能状态对象”。

---

## 1. Process（流程定义）

**定义**：一条业务流程的可复用蓝图。它声明：每次 Run 由哪些**步骤（Tool）**组成、以什么**顺序/阶段（stage）**排列、步骤间有什么**依赖（`depends_on` 控制 / `consumes`·`reads` 数据 / Condition 跨流程）**、简单的执行 guard（`when`）、以及附加的**策略**（重试、资源、SLA、人工卡点）。**单次 Run 的步骤依赖图是 DAG；完整业务生命周期可由多个 Run 构成循环。**

**职责**：作为"该怎么跑"的唯一事实来源；可被静态校验（step ID/引用/产物/资源是否合法、依赖是否成环）；可被可视化；可被非工程角色（如风控、合规）审阅。

**不是什么**：不是一次运行（那是 Run）；不含具体业务数据（那在 task/Context）；不写在命令式代码里（P1 立场）。

**关系**：Process 定义与既有的 session/task 业务实例在创建 Run 时汇合：Run 冻结所采用的 Process/Registry/Profile 版本，session/task 拥有该 Run。Process 本身不创建、不包含业务实例。Process 通过注册表引用一组 Tool。

**参考实现**：`ToolLibrary/*/tool_registry.json`（步骤清单 + stage + 依赖 + 产物）；`registry_normalizer.py:normalize_registry` 归一化并校验；`koubo_storyboard/constants.py:21-52` 固化口播主链路的步骤顺序。

---

## 2. session + task（业务实例，一分为二）

一次业务实例（一笔贷款申请、一个尽调标的、一条待生成的视频、一次月度报告）由两个**语义上不同的职责**构成：**session**（身份/文件/事件）与 **task**（业务记录）。

> **统一口径（团队审核）**：**FlowSpec 在语义上始终区分 session 与 task 两种职责**；**具体实现可以分表、分对象，也可以物理合并在一条记录里，但不得混淆这两种职责**。OpenCrew 采用物理分表（`sessions` + `openclip_tasks`），这是它的取向；其他域可自行选择物理形态。

### 2.1 session（身份与文件载体）

**定义**：一次业务实例的**身份锚点**——拥有 **Workspace**（所有文件的根）、**事件流**（`session_events`，可观测与 UI 的来源）、以及贯穿始终的稳定身份 `session_id`。

**职责**：给整条业务实例一个稳定 ID，分发给下面每个 Run/Step/Event 作为 join key；持有工作区路径；承载事件与状态同步。

**关系**：一个 session ⇄ 一个 Workspace；在 FlowSpec 业务实例内，session 与 task 语义 1:1 配对；该业务实例在首次执行前可有 0 个 Run，执行后可累积多个不可变 Run。

### 2.2 task（业务记录）

**定义**：与 session **配对（1:1）** 的**业务创作/输入记录**——业务字段都在这：申请人信息 / 标的信息 / 视频文案 / 模型选型 / 版本指针 / 当前方案。

**职责**：保存"这次业务要做成什么样"的全部业务语义；持有指向当前版本与最近一次 Run 的指针。

**关系**：task ⇄ session 一对一；task 提供业务字段，session 提供文件与身份。

### 2.3 为什么拆（这条模式的价值）

| | session | task |
|---|---|---|
| 管什么 | 文件 / 事件 / 会话身份 | 业务字段 / 选型 / 版本指针 |
| 变更频率 | 低（身份稳定） | 高（业务反复编辑） |
| 谁在读 | 运行时、UI 事件、文件层 | 业务逻辑、审批、报表 |

**边界提示**：`session` 与 `task` 这两个词在 OpenCrew 里已经**超载**——`session` 还指 `tool_use_session`（一次 Run）与 `opencode_session_id`（外部会话）；`task` 还指 `task_runs`（通用后台作业）与 `media_library_tasks`。本规范中 session/task **专指业务实例这一层**；"一次编排运行"一律称 **Run**（见 §3），避免与 `tool_use_session` 撞名。

> **语义恒区分、物理可合并**：session/task 是本规范业务实例层的**主词**（语义上恒区分两种职责），但**不规定物理存储**——可分表、可合并成一条记录，只要不混淆职责。现实里 `sessions` 本身也带 `title/command/status` 等**可变字段**（非纯"低频身份载体"），也并非每个 session 都必然有配对 domain task——所以把 session/task 当"强制二元存储模型"是过度约束。
>
> **配套契约（无论分表还是合并都要定义）**：**配对创建**（session 与 task 一致地建立）、**归档/删除**（一致地下线，避免半删）、**孤儿修复**（有 session 无 task 或反之时的收敛策略）、**事务边界**（两者跨越的写操作的原子性/补偿）。

**参考实现**：`sessions` 表（`workspace_dir` / `session_events`）+ `openclip_tasks`（1:1，`UniqueConstraint(session_id)`，业务字段 + `current_*_version_id` / `latest_attempt_id`），口播特化在 `talking_head_task_configs`。join 逻辑见 `koubo/repository.py:get_task/list_tasks`。`Variables.opencrew_session_id` / `task_id` 即这层身份。

---

## 3. Run 与 Step Attempt（两个概念，不是同义词）

> **统一口径**：业务执行/返工层用 **Run**，单步技术重试层用 **Step Attempt**。OpenCrew 的历史表名 `openclip_attempts` 实际承载一次业务运行记录，映射到 FlowSpec Run；不要仅凭表名把它映射成 Step Attempt。

**Run（一次流程运行）**：对某对 session/task 的**一次 DAG 编排执行**（FlowSpec 概念；OpenCrew 可由 `openclip_attempts` 与其 `tool_use_session_id` 联合承载，当前命名尚未完全收敛）。一个 Run 是一组有依赖关系的 Step 的容器；它的步骤状态、产物版本、耗时统计都归属于这个 Run。业务补件、人工返工、数据修正不在本 Run 内画回边，而是创建新 Run，并以可选 `supersedes_run_id` 指向被替代的 Run、以 `input_revision_hash` 标识本次输入版本（均 `[proposed]`）。

**Step Attempt（技术调度尝试）**：某个 Step 被适配器执行的第几次——不是“又一次 Run”。`attempt_no` 每次 Step 调度递增；同一 Step Attempt 内的网络超时、限流或可恢复 5xx 可以发生 transport retry，但这些 retry 不新增流程节点。自动技术重试不得改变同一逻辑操作的 `operation_idempotency_key`；只有显式重新计算或输入版本变化才产生新的 operation key。当前 OpenCrew 的步骤键仍把 `retry_count` 编入 `idempotency_key`，是 `[implemented]` 现状而非目标语义（见 [09·L4.5](./09_ProductionLessons.md)）。

**Checkpoint（恢复产物）**：长批次 Tool 可在声明的安全完成边界发布 checkpoint。它记录输入快照哈希、Tool 契约版本、完成边界、恢复 token 与状态文件校验和；后续 Step Attempt 只有在这些绑定仍匹配时才能恢复。Checkpoint 不表示成功、不改变 DAG，也不等于 pause；它只是一次重新执行可选择的、可校验输入。

**关系**：一个业务实例可有 0..N 个 Run；一个 Run 含 1..N 个 Step；一个 Step 可有 0..N 个 Step Attempt，并可关联多个不可变 Checkpoint。Run 绑定恰好一份 Context/Variables 实例；返工 Run 可通过 `supersedes_run_id` 串成可审计的修订链。完整基数见 [0.2](#02-权威层级基数与包含关系)。

**成本边界**：Run 是成本、用量和耗时的**聚合范围**，不是“一创建就计费一次”的统一边界。真正的计费/去重边界是 Tool 内带稳定 `operation_idempotency_key` 的 billable operation；一次 Step Attempt 可以不产生收费、产生一次收费，或包含多个 fanout 子项收费。这样重跑 Run、复用已完成步骤和外部请求去重才不会被混为一谈。

**参考实现**：Run 的业务记录主要落在 `openclip_attempts`（`db/schema.py:1069`，历史命名）并通过 `tool_use_session_id` 关联执行目录 `tool_use_sessions/<tus_id>/`；Step Attempt 体现在步骤 `retry_count`。`Variables.current_attempt_id/attempt_no` 沿用 OpenCrew 历史字段名，属于实现绑定而非概念层命名依据。

---

## 4. Step（步骤）

**定义**：Run 中的一步**逻辑调度节点**。Step 引用一个 Tool 契约，但不等价于“一次实际调用”：guard 为假时不调用 Tool；Confirm Step 会创建并等待 Human Task；fanout/Agent Step 的一个 Step Attempt 可包含多个子项、Model Invocation 或 Tool Call。Step 是编排/依赖边界，Step Attempt 是技术调度边界，billable operation 才是副作用去重与计费边界。

**职责**：声明自己的**输入依赖**（读哪些变量 `reads_session_context`、消费哪些上游产物 `consumes_outputs`）、**输出**（产物 + 可写哪些变量 `writes_session_context`）；拥有一块**私有工作目录**；执行完返回结构化结果（产物路径 + 变量补丁 + 指标）。

**不是什么**：不是 Tool 本身（Tool 是可复用的可执行体，Step 是它在某个 Run 里的一次具体调用）；不直接改共享状态（只能提交白名单补丁）。

**关系**：Step 由 Tool 驱动；读/写 Context；产出 Artifact；发 Event。步骤间的耦合分三类（[00·P9]）：**数据依赖**走 `consumes`/`reads`（经 OutputManifest/Variables），**流程内控制依赖**走 `depends_on`（引用上游 step_id + 期望状态），**跨流程/外部信号**才走命名 Condition。Step 可带最小 `when` guard；guard 为假时进入 `skipped`，不调 Tool。

**参考实现**：`runner.py:RunnerStep`（`step_id/tool_id/step_index/status/retry_count/idempotency_key/heartbeat...`）；持久化为 `State.json` + `Output/OutputManifest.json`；私有目录 `S{index}_{tool}/{Working,Output,Report,Prompt}`。

> **两层 Step 的说明**：在 OpenCrew 里 "Step" 有两个尺度——平台层的 `S{n}_` 工具步骤（本节所指），和口播 UI 层把一个 plan 再拆成的 **slot**（`audio→image_prompt→image→video_prompt→raw_video→final_video`，见 `slot_state_services.py`）。规范上二者是**同一概念的不同粒度**：都遵循"有序、有状态、有依赖、有产物"。UI 弹窗渲染的是更细的 slot 粒度。

---

## 5. Tool（工具）

**定义**：步骤背后**真正干活的可执行体**——一段脚本、一个服务调用、或一次人工任务。Tool 用**声明式契约**描述（它读什么、写什么、产什么、花多少钱、用不用 LLM），由**适配器**负责真正执行。

**职责**：干一件定义明确的事，并遵守契约——只读自己声明要读的、只写自己声明拥有的、把产物写进 OutputManifest。Tool **不感知编排**：不知道自己是第几步、下一步是谁，只拿到 `paths`（workspace/context 目录）和自己的输入。

**不是什么**：不是编排器；不直接访问业务数据库；不直接改别人的变量。

**关系**：Tool 被 Process 引用；在 Run 中被实例化为 Step；通过 `context_patch` 影响 Context（受白名单约束）。

**参考实现**：`tool_registry.json` 里的一条（`id/name/script/stage/hard_dependencies/main_outputs/uses_llm/cost_level`）；执行走 `runner.py:ToolAdapter` 协议——`SubprocessToolAdapter`（把脚本当子进程跑，cwd=workspace，env 被 scrub）或 `NoopTool`。真正的脚本本体在 `ToolLibrary/<域>/*.py`。

**工具类型（建议分类，采众家之长）**：`Script/Command`（跑脚本，Control-M）、`Service/Activity`（调外部服务，Temporal Activity）、`Confirm/Gate`（人工卡点，BPMN User Task）、`Suspend/Wait`（等外部回调，SFN waitForTaskToken）、`SubProcess`（调子流程，Control-M 子文件夹）。

### 5.1 AI 执行、Usage 与 Cost（一等概念）

**Model Invocation** 是一次实际提交给模型/provider 的调用，也是 LLM token 或媒体单位的最小计量、计费和去重边界。**Agent Execution** 是一个 Step Attempt（或 fanout item Attempt）内至多一次的有界多轮执行，包含 `1..N` 个 Model Invocation 和 `0..N` 个 Tool Call；Agent 内循环不成为 Process 回边。若业务上确有多个可独立失败、恢复或授权的 Agent，应拆成不同 Step/fanout item Attempt，而不是在一个 Attempt 下产生多个 `agent_execution_id`。OpenCode Session 是可选对话上下文，不是业务 session。完整约束见 [10](./10_AI_ModelAndAgent_Profile.md)。

**UsageRecord** 是每个 billable operation 的不可含糊计量事实：关联稳定 `operation_id/idempotency_key`、Model Invocation、Run/Step/Attempt、provider/model/request ID，记录 input/output/cache/reasoning token 及图像/音频/视频等 provider units。provider 未返回用量时必须记 `unavailable`，不能写 0 冒充没有消耗。

**Cost** 与 Usage 同记录但分开定性：`unknown | estimated | provisional | final`。估算要绑定价格快照；最终值来自 provider 账单/发票对账。金额使用十进制定点字符串 + ISO 币种，不用二进制浮点。若平台还计算 customer/internal **Charge**，它必须是独立字段，不能覆盖 provider cost 或把毛利混入成本。纠错以新记录 `supersedes_usage_record_id` 关联旧记录，保留审计链。

**Budget** 是调用前约束（Run/Step/租户可分层），Usage/Cost 是调用后事实。调度器可以预留估算预算、执行后按实际结算；预算耗尽返回结构化错误。Step、Run、session 与租户看板都从 Model Invocation 级记录聚合，不能只保存一个不可对账的 `run.cost` 总数。

正式目标契约：[`AIExecutionProfile.schema.json`](./schema/proposed/AIExecutionProfile.schema.json) 与 [`AIUsageRecord.schema.json`](./schema/proposed/AIUsageRecord.schema.json)。

**OpenCrew 参考实现 `[implemented/partial]`**：`local_usage_log` + `LocalUsageRecorder/local_metering` 已持久记录开放 units、`est_cost_micros`、币种、`pricebook_version` 和幂等键。精度边界必须保留：`actual_cost_micros` 列在默认计量路径留空；当前 xAI 视频调用是明确例外，会从 provider usage 写入 actual cost/source/raw，但尚未覆盖所有调用路径，也不是发票对账结论。pricebook 本体是代码配置而非表；customer/internal charge 在报表期按倍率计算且不落表。目标契约是在这套基础上补统一 Invocation/Agent 身份、typed 状态、不可变纠错记录与预算，而不是把“有列/有一个写入例外”误写成“已有完整实际费用与结算闭环”。详见 [07·3.A.8](./07_ImplementationBinding.md)。

---

## 6. Variable / Context（变量与上下文）

**定义**：一个 Run 范围内**强类型的共享键值状态**。它同时是"参数"（门控步骤能否跑、怎么跑）和"结果通道"（步骤把关键结论写回，供下游读）。Context 是**变量的载体**——即那份被校验的 `Variables` 文档。

**职责**：以**强类型 + 禁止额外字段**的方式集中保存跨步共享状态；对**读**做依赖校验（步骤声明的 `reads` token 必须在 Context 里齐备）；对**写**做白名单门控（步骤只能改自己 `writes` 声明拥有的字段）。

**不是什么**：不是大文件的搬运通道（大产物走 Workspace/Artifact，P4）；不是隐式全局命名空间（有作用域阶梯，P5）。

**作用域阶梯**（借 Control-M）：`Step-local`（步骤私有）→ `Run/Context`（本次运行共享）→ `Named-Pool`（跨相关运行）→ `Global`（跨环境）。默认最小可见，越往上越需显式声明。

**关系**：Step 读/写 Context；Context 归属一个 Run；写入以 `context_patch` 提交、经 `merge_context_patch` 校验合并。

**参考实现**：`tool_sessions/schemas/models.py:Variables`（pydantic `ContractModel`, `extra="forbid"`），落盘 `0_SessionContext/Variables.json`；`prepare.py:prepare_session_variables` 播种；`runner.py:merge_context_patch` 按 `writes_session_context` 白名单校验合并；`runner.py:check_dependencies` 按 `reads_session_context` 校验读依赖。

---

## 7. State（状态）

**定义**：单个 Step 或整个 Run 的**生命周期状态**。关键点：状态不只是"跑没跑完"，还包括"**卡在什么上**"——等条件、等资源、等主机、等人工确认，各是不同的显式状态（借 Control-M 的可观测 wait-states）。

**职责**：让"现在到哪了 / 为什么没动"可查询、可驱动 UI、可触发恢复。

**状态机（规范基线）**：
普通自动 Step 走 `not_started → running → {completed | failed | blocked}`；Confirm/Suspend Step 可走 `running → waiting(user|external_callback) → running → completed`；guard 为假或依赖已不可能满足时进入 `skipped`（`[proposed]`，不调 Tool）。另有存活异常态 `stale_running`（心跳超时）与 `orphaned`（worker 死亡）。Run 不是等待“所有 Step completed”：目标 Runtime 先按 `skip_unreachable` 闭合未激活分支，再要求恰好一个 completion outcome 的终点 Step 与必需 Artifact 成立。

**不是什么**：`blocked` 不该是一个不透明黑箱——必须能查出到底卡在哪个 condition/resource/predecessor（Control-M 的 Waiting Info 教训，P 见 08）。

**关系**：State 属于 Step/Run；由 Runtime 推进；变化时发 Event。

**参考实现**：`schemas/models.py:State`（`status` 默认 `not_started`）；转移写在 `runner.py`（`running`→`_finish_step` 的 `completed/failed/blocked`；心跳超时 `recover_stale_running_steps` → `stale_running`）；口播 UI 层的 slot 状态用颜色枚举 `green/white/gray/yellow/red → done/pending/disabled/running/failed`（`slot_state_services.py`）。目标规范不把颜色当权威，而是分别保留 execution status 与 Artifact validity，再由 UI 投影。

---

## 8. Workspace 与 Artifact（工作区与产物）

**定义**：**Workspace** 是一个 session 的文件根目录；**Artifact** 是步骤产出的文件；**OutputManifest** 是"某步产了哪些文件"的清单，是步骤间文件交接的契约。

**职责**：Workspace 给每个 Run、每个 Step 划定私有子目录，避免互相覆盖；Artifact 作为一等公民（P4），下游步骤通过上游的 OutputManifest 按名索取文件，而非靠约定俗成的路径猜测。

**最小充分边界**：只有跨 Step 交接、跨 Run 复用或正式发布的 canonical output 才是业务 Artifact。Runtime 在 finalize 自动生成版本 `artifact_id`、计算内容 `sha256/size`，并通过 producer `attempt_id` 关联该 Attempt 唯一保存的 `input_snapshot_hash`；流程作者只在确需 join/re-bind 时声明业务 `binding_keys`。Working、诊断日志、Prompt、staging 和可重建投影不是业务 Artifact，不承担这套 binding/派生元数据。binding 只需在业务对象与产物角色范围内稳定，不是全平台永恒 ID。

**关系**：Workspace 归属 session；Run/Step 是其子目录；OutputManifest 由 Step 产出、被下游 Step 的 `consumes_outputs` 消费。

**参考实现**：`sessions.workspace_dir`（DB）/ `Variables.workspace_dir`；布局见 `paths.py:ensure_tool_session_layout`（`0_SessionContext/`、`SessionOutput/{manifests,media,...}`、每步 `S{n}_*/`）；`runtime.py:workspace_for(task)` 解析；依赖检查靠 glob `S*_*/Output/OutputManifest.json`（`runner.py`）。详见 [05](./05_Workspace.md)。

---

## 9. Condition（跨流程/外部命名信号）与 depends_on（流程内控制依赖）

> **定位（[00·P9]）**：**流程内**的"步骤 A 完了才能跑 B"用 **`depends_on`**（引用上游 step_id + 期望状态，支持 `any_of` 做 OR-join），**不**用命名条件——这样保留静态 DAG 校验与血缘。**命名 Condition 只用于跨流程 / 外部事件信号**。

**depends_on（流程内）**：`[{ "step_id":"S1", "statuses":["completed"] }]`，多项默认 AND；OR-join 用 `[{ "any_of":[...] }]`。数据依赖能覆盖顺序时（有 `consumes`）无需再写 depends_on。依赖关系必须无环。

**when（流程内简单分支，`[proposed]`）**：只支持单变量的 `equals` / `not_equals` / `in` / `exists`。前三者引用的变量尚不存在时等待 `variable`；`exists` 直接以“是否存在”求值。条件为假时步骤成为终态 `skipped`；当上游都已终态且某依赖/Artifact/变量已不可能满足时，下游也按 `skip_unreachable` 递归关闭。v0.4 不提供任意表达式 DSL 或 Gateway。

**Condition（跨流程/外部）**：命名的依赖信号；生产者向共享**条件池**发布、消费者等待，双方只引用条件名。适合跨流程/跨域（加命名前缀）与外部事件。是 `blocked/waiting` 的一种原因。

**布尔逻辑**：流程内依赖只支持显式 AND（多项全齐）与 `any_of` OR；跨流程 signal 可组合 AND/OR，但 v0.4 不标准化任意手写表达式。

**参考实现**：OpenCrew 当前用**依赖桶**近似——`registry_normalizer.py:DependencyBuckets`（`reads_session_context / consumes_outputs / producer hints`），由 `runner.py:check_dependencies` 求值。
> 🆕 规范建议：把"依赖桶"升级为显式**命名条件池**（Control-M 模型），使跨流程依赖与"等什么"可查询更彻底。见 [06](./06_Runtime_Observability.md)。

---

## 10. Resource（资源）

**定义**：步骤运行的**前置资源约束**，三种**互不相同**的原语（采纳团队审核对 [00·P8] 的修正）：
- **`mutex`（互斥锁）**——命名锁，`exclusive`/`shared`。解决**正确性**：别让两步同时写同一文件/表。
- **`semaphore`（计数信号量）**——**并发数**上限（GPU 卡数、DB 连接数），每步声明占用量；有余量才放行。
- **`rate_limit`（速率限制）**——**单位时间量**上限（LLM tokens/分钟、API QPS）。**与信号量不同**：速率≠并发数。

**职责**：在昂贵/受限资源上做正确性隔离与并发节流。对目标域尤其重要——GPU/LLM 步骤既贵又有并发上限。

**关系**：Resource 是 Step 的一种前置条件（与 Condition 并列，共同决定"能不能跑"）。

**参考实现**：🆕 规范建议（借 Control-M 的 Control Resources + Quantitative Resources）。OpenCrew 当前靠 runner 并发上限做粗粒度限流，尚无命名资源池。见 [06](./06_Runtime_Observability.md)。

---

## 11. Event（事件 / 可观测）

**定义**：步骤与运行的**状态变化广播**。既是给人看的可观测流，也是驱动 UI（弹窗进度、监控面板）与触发下游动作的信号。

**职责**：让"发生了什么"可追溯、可订阅，并可按 cursor 向 UI/消费者重放时间线。这里的“重放”是**事件投递/审计时间线重放**，不是用 Event 重建权威状态；FlowSpec 仍以物化状态为权威，不要求事件溯源。事件带足够的关联键（session/task/run/step/tool/step-attempt）以便过滤与串联。

**两种投递**（规范都要支持）：**轮询快照**（拉当前状态 JSON）与**事件流**（SSE/长轮询增量推送）。

**关系**：Event 由 Step/Run 的 State 变化产生；被 UI 与监控消费；里程碑事件也可作为 Condition 的载体。

**参考实现**：`session_events` 表（带 `step_id/tool_id/attempt_id/workflow_id`）；通用 runner 发 `tool_session.step.{started,completed,failed,blocked,stale_running}`（`runner.py:RunnerEventSink`）；平台提供 `GET /api/sessions/{id}/events?since=`（轮询）与 `/events/stream`（SSE）。
> **前端事实（[implemented]，已证实）**：口播三个执行弹窗当前订阅的是**HTTP 状态快照轮询**（`video_plan_execution_payload`），**不是** session SSE——视频/图片计划每秒轮询（`KouboVideoPlanModal.jsx:396`、`KouboImagePlanModal.jsx:331`），纯视频计划运行时约 1.6s、非运行时 5s（`KouboVideoOnlyPlanModal.jsx:236`），快照 API `kouboStoryboardApi.js:184`。SSE 端点虽在，这些弹窗未用；未来可切 SSE。详见 [06](./06_Runtime_Observability.md)。

---

## 12. 关系速查（谁依赖谁）

```
Process  —冻结为—▶ Run.definition_snapshot；—定义—▶ Step DAG（引用 Tool；无原始回边）
session  —拥有—▶  Workspace + Event Stream；—语义 1:1 配对—▶ task
业务实例 —含—▶    Run[0..N]；Run —supersedes—▶ 前一 Run[0..1]
Run      —含—▶    Step[1..N]；—绑定—▶ Context[1]
Step     —调度—▶  Step Attempt[0..N]（when=false/skipped 时为 0）
Attempt  —执行—▶  operation / Invocation[0..N]；—产出—▶ Artifact / Checkpoint[0..N]
         —读/写—▶ Context（reads/writes 声明，写走白名单补丁）
         —产出/消费—▶ Artifact via OutputManifest（数据依赖）
         —前置—▶  depends_on（流程内控制）/ Condition（仅跨流程） + Resource（三原语）
         —变化时—▶ Event —▶ UI/监控
State    —归属—▶  Step / Run
```

本节是便携速查；基数、逻辑包含和正常/返工时序以 [0.1–0.3](#01-先分清四种关系) 为准。

下一步：想定义流程读 [02](./02_ProcessDefinition.md)；想写工具读 [03](./03_ToolContract.md)；想理解变量与状态读 [04](./04_VariablesAndState.md)。
