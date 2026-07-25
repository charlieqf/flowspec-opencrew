# 00 · 总览：目标、范围与设计取向

## 1. 目标

给"一步步跑完的业务流程"提供一套**领域无关、AI-heavy 场景优先**的建模规范，使得同一套要素、同一套心智模型、同一套可实现绑定，能够描述并支撑：

- 社交平台短视频 / 口播视频创作（本规范的来源域）
- 贷款审批流程（多节点审批 + 人工卡点 + 风控规则）
- 银行数据挖掘与报告生成（取数 → 清洗 → 建模 → 报告）
- 金融机构数据 ETL（大批量、可重跑、有依赖）
- 大规模尽调材料自动处理（海量文档摄入 → 抽取 → 校验 → 汇总，人工复核）

一句话：**把"业务流程编排"从每个项目各写一遍的隐式代码，收敛成一套可复用、可推理、可观测的显式契约。**

## 2. 适用范围（什么该用它，什么不该）

**适合**：
- 由**离散步骤**组成、步骤间有**依赖**、每步是一段**可独立执行的工作**（脚本/服务/人工）。
- 步骤**耗时且昂贵**（GPU/LLM/大数据/外部 API），需要**可重跑、可恢复、可限流**。
- LLM/多模态/Agent 调用需要冻结模型与 prompt、限制权限和预算，并逐次核算 token/媒体用量与费用。
- 需要**人工卡点**（审批、复核、确认）与**可观测**（现在跑到哪、卡在哪、为什么卡）。
- 产物包含**文件/artifact**，需要在步骤间可靠交接。

**不适合**（用别的工具）：
- 毫秒级、高频、纯内存的同步计算 —— 那是函数调用，不是流程编排。
- 无依赖、无状态、无产物的一次性脚本 —— 直接跑就好。
- 强一致分布式事务 —— 用数据库事务 / Saga，不在本规范目标内。

## 2.1 核心对象如何关联（先看层级，再看细节）

FlowSpec 不把 `session/task/run/step/attempt` 当成一串近义词。它们分别属于业务身份、业务执行、逻辑编排和技术调度四个边界：

```text
Process@version + Tool Registry + AI Profile
                 │ 创建 Run 时冻结定义快照
                 ▼
业务实例 = session [1] ⇄ task [1]          session 拥有 Workspace / Event Stream
                 │ 包含 0..N 个不可变 Run；业务修订创建下一 Run
                 ▼
Run [1] ──包含──▶ Step [1..N] ──调度──▶ Step Attempt [0..N]
  │                  │                         │
  │                  │ skipped 时无 Attempt   ├─ operation / Model Invocation [0..N]
  │                  └─ DAG 依赖决定先后       ├─ Artifact / Checkpoint [0..N]
  └─ Context [1]                               └─ Usage / diagnostic facts [0..N]
```

- `session ⇄ task` 是**语义 1:1 配对**，不是两套父子目录；实现可分表或合并。
- business instance 在首次执行前可有 0 个 Run；补件、重审、返工通过 `supersedes_run_id` 新增 Run，旧 Run 不覆盖。
- Run 含 Process 定义的逻辑 Step；Step 即使被 guard 关闭也保留 `skipped` 状态记录。
- Step Attempt 是一次技术调度。网络 transport retry 可留在同一 Attempt；重新调度才新增 Attempt。
- Step 不等于一次真实调用；Agent/fanout 的一个 Attempt 可包含多个 Tool Call、Model Invocation 和计费 operation。
- Artifact、UsageRecord 和 Event 通过稳定 ID 关联上述层级，是证据平面，不是另一套可变执行树。

完整基数、包含关系、执行与返工时序见 [01·0.1–0.3](./01_ConceptModel.md#01-先分清四种关系)。

## 3. 本规范采取的关键设计取向（Design Positions）

编排系统在几个根本问题上存在真实分歧（详见 [08](./08_PriorArt_CrossReference.md)）。本规范**明确选边**，因为不选边的规范无法指导实现。每条都注明"为什么这么选"，你若换域可据此判断是否需要改立场。

| # | 分歧 | 本规范的立场 | 为什么 |
|---|---|---|---|
| P1 | 拓扑：声明式 vs 命令式 | **每次 Run 展开的步骤依赖图是声明式 DAG**（步骤/依赖是数据，Run 内禁止原始回边）；但同一 `session + task` 的完整业务生命周期可通过多个 Run 形成“退回→修订→重审”的循环。**有条件通过**：当前只实现了注册表归一化 + 单步执行。 | 既保留真实业务循环，又让每次执行可静态校验、可恢复、可推导关键路径 |
| P2 | 持久化：元数据 vs 事件溯源 | **改为**：持久**物化状态 + append-only 审计事件**（不要求事件重放）；**transactional outbox 为 `[proposed]`**。~~"可变 State.json 天然可审计"~~ 不成立——单个可变文件不宜独担并发下权威状态。 | 断点续跑要，但审计靠 append-only 事件，不靠可变文件 |
| P3 | 步骤是否需确定性 | **不要求确定性，要求幂等**（澄清后通过）。幂等 = 同一逻辑请求被**去重/重放/对账**，**不**表示随机/LLM 步骤重算得等价输出。每步声明 `pure/idempotent/reconcilable/non_idempotent`；技术重试沿用稳定 `operation_idempotency_key`，`attempt_no` 只做观测。 | 步骤要调 LLM/外部 API；避免把每次尝试误当新业务操作 |
| P4 | artifact 是否一等公民 | **一等公民，但只治理 canonical output**（采 Argo 模型，**通过**）。Runtime 在 finalize 生成版本 ID/内容 hash，派生摘要只存 producer Attempt；业务 binding 仅在 join/re-bind/跨 Run 对齐时声明。Working、日志、Prompt、staging 与投影排除。 | 保住错绑/陈旧复用防线，同时避免给所有文件强加身份与哈希税 |
| P5 | 步骤间数据共享 | **强类型上下文 + 白名单写入**（通过，放宽"唯一写者"）：默认唯一 owner；确需多写者时允许显式 `reducer`/`CAS`。Named-Pool/Global scope 标 `[roadmap 🆕]`。 | 最小权限；避免隐式全局命名空间互踩 |
| P6 | 人工介入 | **一等 Human Task**（目标契约通过，OpenCrew 通用实现仍属 roadmap）。`confirm` Step 被调度后创建持久 work item 并进入 `waiting(user)`；决定须绑定 input hash、角色、actor、reason 与 expected revision。`human_gate` 是 work item 配置，**不是** dispatch 前必须已满足的谓词。 | 避免“还没创建审批任务，却要求先审批”的循环依赖；让责任与并发冲突可审计 |
| P7 | 重试默认姿态 | **步骤默认不重试、Run 默认停止启动新步骤**：`RetryPolicy` 只有 `fail_fast/on_transient`；仅 `pure/idempotent` 的瞬态错误可有界重试。与它正交的 `FailurePropagation` 只有 `stop_run/continue_independent`，后者只放行 DAG 上可证明不受失败影响的分支；失败步骤的后代始终阻断。`reconcilable` 走对账，`non_idempotent` 一律 fail-fast。 | 把“是否重试本步”与“失败后其他分支是否继续”拆开，避免一个 `fail_fast` 同时承担两种语义 |
| P8 | 资源治理 | **改为三原语**：`mutex`（互斥）+ `semaphore`（并发数）+ `rate_limit`（单位时间量）。`llm_tokens_per_min` 是 rate limit 不是 semaphore。并定义 lease/TTL/fencing/公平性。 | 三者是不同问题 |
| P9 | 依赖与完成语义 | 流程内用**直接 typed dependency**（多项=AND、`any_of`=OR）+ 最小 `when` guard；guard 为假或已不可能满足的下游分支按 `branch_closure=skip_unreachable` 递归关闭为 `skipped`。Run 以 `exactly_one_outcome` + 终点 Step + 必需 Artifact 完成。跨流程/外部事件再用命名 signal。 | 覆盖非线性 DAG，避免未激活分支永久 pending，也避免把“所有 Step completed”误当唯一业务完成条件 |
| P10 | SLA / 截止 | **overlay，与单步超时分离**（作为 **roadmap 通过**）。关键路径尽量从 DAG + 历史耗时**推导**，不手工维护；补时区、业务日历、暂停计时、预测置信度。 | 业务截止是服务级承诺 |
| P11 | 日志与物理存储 | **事实分平面、路径有基准、布局增量兼容**：状态/审计 Event/Usage-Cost 以数据库为各自权威，高容量 Step Attempt 诊断与 Artifact 落 Workspace 或对象存储，服务日志进入部署 sink；保留 `tool_use_sessions / S{index}_{tool} / Working|Output|Report|Prompt` 主骨架，只增加 Attempt 隔离、staging 与 StorageIndex。 | 避免从文本日志反推业务事实，也避免为“统一”另造一棵与 OpenCrew 并行的目录树 |

> **P2 权威边界补充**（团队审核要求明确）：物化状态与审计事件**各有权威范围**——`OutputManifest`（产物真相）、`State.json`（步骤状态）、DB 投影（可查询索引）、事件流（时间线）。**关键**：**append-only 审计事件 ≠ 事务 outbox**。当前 runner 是**依次**写 `OutputManifest` → `State.json` → `SessionRunSummary` → **再单独** `_emit_event`（`runner.py:824` 的 `_finish_step`），**不具备原子 outbox 语义**——任一步之间崩溃会产生瞬时不一致，靠 L2/[06·E] 的重读/对账/重启对账收敛。真正的事务 outbox 是 `[proposed]`。

> **v0.4 拓扑边界（防过度设计）**：**业务生命周期可以有环，单次 Run 的可调度依赖图保持 DAG**。同一输入下的瞬态失败用 Step Attempt / `retry`，集合处理用 `fanout`；补件、返工、数据修正创建同一 `session + task` 下的新 Run，并以 `supersedes_run_id` 关联被替代的 Run。流程图仍可画“退回重审”的回环，但运行时不执行有环依赖图。v0.4 不引入循环网关、`repeat_until` 或任意表达式语言。

> **可执行定义边界**：`contract_level=illustrative` 只用于讲解拓扑；`contract_level=executable` 必须额外冻结 `schema_version`、Tool Registry 与 Context Schema 引用、typed Artifact contracts、分支闭包、完成 outcomes 和 Run budget。Run 开始时再冻结 Process/Registry/AI Profile digest 与输入修订哈希。四套 demo 以此边界证明定义、运行事实和 UI 能由同一份契约导出。

> **恢复边界补充**：一次 Step Attempt 内可以有同一外部操作的 transport retry；Step 重新调度产生新的 Step Attempt；Checkpoint 是后续 Step Attempt 可读取的恢复产物，不是新的图节点或 Attempt 层级；整链重跑产生新 Run。Run 只做成本聚合，真正的计费/去重边界是带稳定 operation key 的 billable operation，不能把 Run 或 Step Attempt 一概视为收费一次。

> **用户选择的运行范围**：规范支持全链、运行到指定 Step、从指定 Step 重跑和隔离单步诊断，但它们必须被编译为 **DAG 闭包**，不是按展示序号切片。“运行到 T”取 T 的全部必要前置；“从 S 重跑”取 S 与受影响后继，并只复用经 input hash、Artifact schema/hash/binding 和冻结定义验证仍有效的独立上游。canonical 部分运行到边界后进入 `waiting(user)`，不能冒充业务 `completed`；单步默认 diagnostic，不发布正式 Manifest。精确定义与 OpenCrew 领域实现边界见 [06·E6](./06_Runtime_Observability.md)。

> **一等 Usage & Cost 补充**：每次模型调用（Model Invocation）都是可独立计费的 operation，必须产出可去重的 UsageRecord；token/缓存 token/推理 token及媒体单位、费用状态、币种、价格快照或 provider 账单来源均为契约数据。Step、Run、session、租户只做聚合。预算是调度前置约束，实际用量是执行事实；`unknown/estimated/provisional/final` 不得混成一个金额。详见 [01·5.1](./01_ConceptModel.md) 与 [10](./10_AI_ModelAndAgent_Profile.md)。

> **日志与目录边界**：`debug/info/warning/error/critical` 描述严重性，`runtime/adapter/tool/stdout/stderr/model_adapter` 描述来源 channel，二者不得混用；durable Event 不受日志阈值过滤。session 拥有 Workspace，task 是数据库业务记录并引用配对 session，不复制一棵 task 文件树；Run 位于既有 `tool_use_sessions/<run_id>`，Step 输入由 `InputManifest`/上游 `OutputManifest` 定位，Attempt 私有中间文件与诊断按 Attempt 子目录隔离，正式输出只能经 staging/finalize 发布。详见 [05](./05_Workspace.md) 与 [06·F5](./06_Runtime_Observability.md)。

## 4. 与成熟体系对照总表

各体系对"同一件事"的叫法。本规范列在第一列，作为领域无关基准词。完整机制对照与出处见 [08](./08_PriorArt_CrossReference.md)。

| FlowSpec（本规范） | Control-M | Airflow | Temporal | Step Functions | Argo | BPMN/Camunda |
|---|---|---|---|---|---|---|
| **Process**（流程定义） | SMART Folder | DAG | Workflow Definition | State Machine (ASL) | Workflow(Template) | Process |
| **session + task**（业务实例） | Order 的业务对象 | — | — | — | — | — |
| **Run**（一次执行） | Order / Run | DAG Run | Workflow Execution | Execution | Workflow 实例 | Process Instance |
| **Step Attempt**（技术尝试，≠Run） | Job rerun 计数 | try_number | Activity Attempt | Retry | retry | Job retries |
| **Step**（一步） | Job | Task / Operator | Activity | Task state | Template / step | Task / ServiceTask |
| **Tool**（工具契约） | Job Type | Operator 类 | Activity 实现 | Resource(Lambda/…) | Container/Script template | Job Worker |
| **Variable / Context** | AutoEdit 变量（作用域阶梯） | XCom | 函数参数/返回 | State I/O + Assign | parameters | Process Variables |
| **Workspace / Artifact** | Agent 工作目录 + OUTPUT | （无原生，走外部） | payload（无原生文件） | （无原生，走 S3） | **Artifacts + 仓库** | （无原生，走外部） |
| **State**（步骤状态） | Wait Condition/Resource/Host/User → Executing → Ended OK/Not OK | Task Instance states | Event History | Execution history | Pod/Workflow status | 事件日志（Zeebe） |
| **Condition**（依赖） | In/Out Condition（Event） | DAG 边 | 代码控制流 | Next / Choice | dependencies | Sequence flow + Gateway |
| **Resource**（资源） | Control(锁) + Quantitative(计数) | Pool | — | — | **Synchronization：mutex + semaphore** | — |
| **Event / 可观测** | Monitoring + Waiting Info | Task Instance + Metadata DB | Event History | Execution events | status | Exporter 流 |
| **重试/恢复** | On-Do / Cyclic | retries | 持久重试 + 超时 | Retry / Catch | retryStrategy | Error boundary event |
| **人工卡点** | Confirm / Hold-Free | **HITL Operator + `awaiting_input`** | Signal | `.waitForTaskToken` | Suspend | **User Task** |
| **SLA** | BIM / SLA Job | **Deadline Alerts** | — | — | — | — |

> Airflow 3.1 起提供 HITL Operators，3.3 起以 scheduler 管理的 `awaiting_input` 状态持久等待且不占 worker/triggerer slot；旧版“Sensor（近似）”对照已失效，详见 [08·§1](./08_PriorArt_CrossReference.md)。空白格 = 该体系没有对应的一等概念。可看出：**session/task 这一层（业务实例身份，且拆成"文件身份 + 业务记录"）** 和 **一等 Artifact**、**三资源原语（mutex/semaphore/rate_limit）**、**SLA overlay** 是本规范相对多数现代引擎的补强点，分别借自 OpenCrew 自身、Argo、Control-M+Argo。

## 5. 版本与演进

- **v0.4**（当前草案）：把四个概念示例升级为 `contract_level=executable` demo；增加 Tool Registry、RunRecord 与 BudgetLedger 目标 Schema，Process 增加 typed Artifact、`skip_unreachable`、`exactly_one_outcome`、Human Task CAS 和稳定 fanout item key；AI Profile 为 1.1，AI Usage 为 1.2（补稳定 Step Attempt 连接和非空计量约束），并覆盖部署/区域、留存/训练、safety/evidence、Agent tool/network、媒体预算、调用生命周期和 observation correction 身份。每个场景提供两次 Run、Mock Artifact/Usage/事件和自包含 HTML。
- **v0.3**：确立“业务生命周期可循环、每 Run 一个声明式 DAG”，补齐恢复层级、失败传播、运行控制、多端一致性、人工 override 与 AI/Agent/Usage/Cost 概念。
- 后续：先把可执行 Process snapshot、Run/Step Attempt 状态、分支闭包和 Human Task 接入 OpenCrew 通用 runtime，再在现有 `local_usage_log` 上补统一 Invocation/Agent 关联与预算，最后扩展资源池、typed fanout 与 SLA。

下一步阅读：[01 · 概念模型](./01_ConceptModel.md)。
