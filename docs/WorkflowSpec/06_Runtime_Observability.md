# 06 · 运行时与可观测性（Runtime & Observability）

> 编排器怎么把定义跑起来：依赖求值、资源治理、心跳存活、失败恢复、事件与 UI、SLA。这里也是从 Control-M 借鉴最多、OpenCrew **建议补齐**最多的一章（🆕 标注）。

## A. 调度循环（Scheduling Loop）`[proposed]`

> **现状（[implemented]）**：OpenCrew 当前是**单步执行器** `runner.py:run_registry_step`——调一次跑一步（`check_dependencies` → dispatch adapter → 合并 patch → finish）。**没有**下面这个自驱动的多步调度循环，"自动从首个未完成步骤恢复推进"也**尚未**是通用能力（口播链路的推进由 router/后台任务按需触发）。下面是**目标形态**。

```
# [proposed] 目标调度循环
loop:
  for step in run.pending_steps():
    activation = evaluate_when(step.when) # [proposed]：普通比较缺值→waiting；exists 直接求值；false→skipped
    if activation.skipped:
      step.status = skipped; continue
    gate = evaluate_prerequisites(step)  # 见 B：依赖/变量/Artifact/外部 signal
    if gate.impossible:
      close_branch_as_skipped(step, gate.reason)  # branch_closure=skip_unreachable
    elif gate.ready:
      claim = try_acquire_resources_atomically(step) # 见 D；检查+获取必须是一个操作
      if claim.acquired:
        dispatch(step); step.status = running
      else:
        step.status = waiting(resource)
    else:
      step.status = waiting(gate.reason) # [proposed]：现行是 blocked，无 waiting（见 04·C）
  recover_stale(run)                    # 见 E
  evaluate_exactly_one_outcome(run)      # 终点 Step + required Artifact
  emit_events(changed_steps)             # 见 F
```

参考实现（单步）：`runner.py:run_registry_step` / `check_dependencies` / `recover_stale_running_steps`。

## B. 门控求值：一步能不能跑（统一谓词）

一个步骤可调度当且仅当**所有前置**满足（把"能不能跑"建模为一组可组合谓词，借 Control-M 的统一 prerequisite 思路）：

```
ready(S) =
      when_true(S.when)                          # 最小 guard；false→skipped，不进入 ready 求值
  AND depends_on_met(S.depends_on)               # 流程内控制依赖（首选；支持 any_of OR-join）
  AND variables_present(S.reads)                # 数据依赖：读变量（04·A5）
  AND artifacts_present(S.consumes)             # 数据依赖：上游产物（05·B3）
  AND conditions_satisfied(S.conditions_in)     # 仅外部/跨流程命名信号（C）—— [roadmap 🆕]
```

`when` 的 `equals/not_equals/in` 引用变量不存在 → `waiting(variable)`；`exists` 直接按存在性求值。guard 为假 → 终态 `skipped`；为真才继续求值其余前置。其余任一不满足时必须区分：生产者仍可完成则 `waiting(reason, detail)`；所有候选生产者均终态且已不可能满足则按 `skip_unreachable` 关闭分支。reason ∈ `{step, variable, artifact, resource, condition, user, host, external_callback}`。**现行实现**：不满足即 `blocked`，无 `waiting/skipped`（[04·C]）。

资源不是只读的 `resources_available` 谓词；“检查可用”和“取得 claim/lease”必须由同一存储原子提交，否则多个 scheduler 会同时看到可用并超发。Confirm 也不是 `human_gate_cleared` 的 dispatch 前置条件：dispatch Confirm Step 才创建 work item，随后进入 `waiting(user)`，见 [02·B2](./02_ProcessDefinition.md)。

### B1. Run 完成与业务 outcome

调度无进展时不能直接判 completed，也不能只等所有 Step completed。Runtime 应先解释每个 unresolved Step：可等待、可关闭，还是定义死锁；递归完成分支闭包后，求值 `completion.mode=exactly_one_outcome`。恰好一个 outcome 的 guard 为真、其终点 Step 均 completed、必需 Artifact 均 valid，Run 才进入 `completed` 并记录该业务 outcome。零匹配、多匹配或仍有无法解释的 Step 都是结构化失败。

## C. 命名条件池（Condition Pool）🆕

借 Control-M 的 In/Out Condition。生产者**发布**命名条件，消费者**等待**，双方不直接引用彼此。

- 生产者步骤完成 → 向共享条件池 `add` 它的 `conditions_out`。
- 消费者步骤的 `conditions_in` 只允许一个扁平、数据式分组：`{"op":"AND|OR","of":["signal.name", ...]}`。v0.4 不接受手写 `expr` 或嵌套表达式；复杂跨流程逻辑应由受版本控制的外部规则服务发布一个新的命名 signal。
- 条件被消费后可 `delete`（避免陈旧）。跨流程/跨域依赖 = 加命名前缀（如 `loan_00123:S1_done`）。

**定位（采纳团队审核 P9）**：命名条件池**不作为唯一依赖模型**。
- **流程内**依赖：优先用**直接 typed dependency**（`consumes_outputs` + `reads`），保留静态 DAG 校验、血缘可推导、避免陈旧条件误触发。
- **跨流程 / 外部事件**：才用命名 signal（命名条件池）。这是它的甜区。

**为什么值得升级**：OpenCrew 现用"依赖桶（`DependencyBuckets`）"隐式表达流程内依赖。命名条件补的是**跨流程/外部事件**这块当前的空白，而非取代流程内的 typed dependency。

```jsonc
// 步骤定义里
"conditions_in": { "op": "AND", "of": ["credit_ready", "kyc_ready"] },
"conditions_out": ["risk_input_ready"]
```

## D. 资源治理：三个独立原语 `[roadmap 🆕]`

采纳团队审核对 [00·P8] 的修正——**别把互斥、并发数、速率三件事混成一个"资源"概念**。三者是不同问题：

### D1. `mutex`（互斥锁）——正确性
命名锁，`exclusive` 或 `shared`。保护共享可变资源（同一文件/DB 表/外部账户），防并发写冲突。经典读写锁。

```jsonc
"resources": [ { "kind": "mutex", "name": "ledger:loan_00123", "mode": "exclusive" } ]
```

### D2. `semaphore`（计数信号量）——并发数
声明**同时在跑的数量**上限（GPU 卡数、DB 连接数），每步声明占用量；有余量才放行。

```jsonc
"resource_pools": { "gpu": { "type": "semaphore", "limit": 4 } }
"resources": [ { "kind": "semaphore", "pool": "gpu", "amount": 1 } ]
```

### D3. `rate_limit`（速率限制）——单位时间量
声明**单位时间**上限（如 LLM tokens/分钟、API QPS）。**这不是信号量**——`llm_tokens_per_min` 是速率而非并发数，混为一谈会算错。

```jsonc
"resource_pools": { "llm_tokens": { "type": "rate_limit", "per_minute": 200000 } }
"resources": [ { "kind": "rate_limit", "pool": "llm_tokens", "amount": 2000 } ]
```

**还需定义（补强）**：lease/TTL（持有租约与超时释放）、fencing token（防脑裂）、公平性（FIFO/优先级）。

**目标域为什么关键**：GPU（信号量）、LLM tokens/分钟（速率）、写账本（互斥）是三种不同约束。OpenCrew 当前仅靠 runner 全局并发上限做粗粒度限流（`min(16, cores-2)`）——三原语细粒度治理是路线图。

### D4. Budget + Usage Ledger（AI 场景一等成本治理）`[implemented/partial] + [proposed]`

`rate_limit` 控制单位时间吞吐，**Budget** 控制某个 Step/Run/session/tenant 最多可花多少，二者正交。调用前按 AI Profile 的 token/金额上限预留预算；每个 Model Invocation 完成或进入“结果未知”后写一条 UsageRecord，按实际用量结算/释放预留。预算耗尽返回 `budget_exhausted`，不能让 Agent 自行缩短后把半成品标完成。

**现行基础 `[implemented/partial]`**：OpenCrew `local_usage_log` 已持久记录 task/attempt/step、幂等键、provider/model/modality、开放 units、`est_cost_micros`、币种、`pricebook_version` 与 reconciliation time，并以唯一幂等索引防重复（`db/schema.py:local_usage_log`）；`LocalUsageRecorder` 事务写入。精度边界：默认计量路径不写 `actual_cost_micros`；当前 xAI 视频路径会从 provider usage 写入 `actual_cost_micros/source/raw`，但不是全路径覆盖或 invoice-final。pricebook 本体是代码配置，不是表；customer/internal charge 是报表期倍率计算，不落表且无 `charge_status/settlement` 列。**目标差距 `[proposed]`**：统一 Model/Agent Invocation 身份、typed measurement/cost status、不可变纠错记录、预算预留/结算和所有调用路径覆盖。

Usage Ledger 的最小粒度是 billable operation，不是 Step Attempt 或 Run。它必须按 `operation_id/idempotency_key` 去重，并保留 `provider_request_id` 供响应丢失和账单对账。provider 不报告 token/费用时分别记 `unavailable/unknown`，不能写零。估算价绑定价格快照；账单修正以 superseding 记录保留审计链。正式目标结构见 [`AIUsageRecord.schema.json`](./schema/proposed/AIUsageRecord.schema.json)。

聚合视图至少支持 `Step / Run / session+task / tenant / provider / model / modality / 时间区间`，分别展示 token、缓存/推理 token、媒体单位和 `estimated/provisional/final/unknown` 金额；provider cost 与 customer/internal charge 分列，不把收入当成本；不同币种在没有明确汇率快照时不得直接相加。

## E. 失败与恢复（Failure & Recovery）

### E1. 重试取向：默认 fail-fast，重试须声明（[00·P7]）

与 Temporal（默认自动重试 Activity）**相反**。规范默认**失败即停、暴露给人**；只有 `pure/idempotent` 步骤遇瞬态错误可显式声明 bounded `on_transient`，且必须给 `max_attempts`（成本越高、次数越少，昂贵步骤仍可选 fail-fast）。`reconcilable` 只能走对账，`non_idempotent` 必须 fail-fast。v0.4 不提供宽泛的 retry-policy `on_error`；下面的 `on_error[]` 是失败动作表，不是 retry policy。

```jsonc
"retry": { "policy": "on_transient",   // fail_fast | on_transient
           "max_attempts": 2,
           "backoff_seconds": 5, "backoff_rate": 2.0, "max_backoff_seconds": 300,
           "jitter": true }
```
重试的退避四参数（初始间隔/退避系数/最大间隔/最大次数）是五大引擎的共识形态，可放心标准化（见 [08](./08_PriorArt_CrossReference.md)）。

执行与恢复有四个不同概念，不能共用一个 `retry_count`：

| 概念 | 边界 | 是否新建 Step Attempt | 是否新建 Run |
|---|---|---:|---:|
| Transport retry | 同一 Step Attempt 内，同一外部 operation 的网络/限流/可恢复 5xx | 否 | 否 |
| Step retry | Runner 再次调度同一 Step | 是 | 否 |
| Checkpoint resume | 新 Step Attempt 读取旧尝试发布的可校验恢复产物 | 通常是 | 否 |
| Run rerun | 从失败步、指定边界或全链重新执行 | — | 是 |

Checkpoint 是恢复输入，不是第五层状态或 DAG 节点。Transport retry 和 Step retry 均须沿用同一逻辑 operation 的稳定幂等键；用户明确重新计算或输入修订才换 operation key。

#### E1.1 Run 级失败传播与 Step 重试正交 `[proposed]`

```jsonc
"failure_propagation": "stop_run" // stop_run | continue_independent
```

- `stop_run`（默认）：某 Step 终态失败后停止**启动**任何新 Step；已经 running 的 Step 不被隐式强杀，除非另发安全 terminate 命令。
- `continue_independent`：失败 Step 的所有 DAG 后代仍为 blocked；只有从失败 Step 不可达、且其他依赖全部满足的分支可以继续，独立性由图推导，不能由人工列表声称。
- 人工修复变量、Artifact 或确认状态后，Resume 前必须重新求值 `when/depends_on/reads/consumes/resources/human_gate`，并按新输入哈希失效下游旧产物。

`continue_on_warning` 不需要成为失败传播策略——warning 默认不等于失败。`allow_partial` 涉及业务最终结果的接受条件，v0.4 暂不标准化，避免把结果语义和调度语义混在一起。

### E2. 声明式恢复策略（On-Do，借 Control-M）🆕

把失败处理从"写在工具里的 try/except"提为**挂在步骤上的声明式策略**：`当<触发> 则<动作>`。

```jsonc
"on_error": [
  { "when": "retry_exhausted",            "do": "notify", "target": "ops" },
  { "when": "exit_code == 137",           "do": "block",  "detail": "OOM，需人工调整资源" },
  { "when": "output_missing",            "do": "set_failed" }
]
```
触发可含：结束状态、退出码、重试耗尽、产物缺失、超时。动作可含：`notify`/`set_failed`/`block`/`add_condition`/`run_cleanup`。**自动重试只由 E1 的 RetryPolicy 触发**，v0.4 不让 `on_error.do=rerun` 建立第二套重试入口；`set_ok` 也不纳入默认动作，避免用策略掩盖真实失败。

### E3. 心跳与"假死"恢复

- 运行中步骤周期性打心跳（`heartbeat_at`）。`[implemented]`
- 超 `heartbeat_timeout_seconds` 未心跳 → `recover_stale_running_steps` 将该 Step 标为 `stale_running` 并写 summary/event。`[implemented]`（通用 runner）。它**不会自动重跑或自动标 failed**；后续恢复动作仍需调用方/目标调度器决定。
- 无活跃 worker 且状态文件过期超宽限 → orphan 处理。`[部分实现]`：**当前仅口播执行子系统**用 `failed` + `orphaned:true` 标志（`video_plan_execution_state_services.py`）；通用 `orphaned` 状态为 `[proposed]`（见 [09·L2.2](./09_ProductionLessons.md)）。

参考实现：`runner.py` 后台心跳线程 + `recover_stale_running_steps`（默认 900s）；口播 `video_plan_execution_state_services.py` 的 orphan 检测（比对状态文件 mtime）。

### E4. 断点续跑（Resumability）`[proposed]`

**目标**：因为状态落 `State.json`、产物落 `OutputManifest.json`，重跑一个 Run 时已 `completed` 且产物齐备的步骤**跳过**，从第一个未完成/失败步骤自动继续。

这里的执行记录分两层：同一 Run、同一 Step 的自动重试，每次落一条新的 **Step Attempt**，不会向依赖图添加回边；Run 已终止后由用户发起的整 Run 重跑，可创建新 Run 并引用前次 Run。两种实现都让被调度的 Step 依赖保持 DAG。

长扇出/抽取可以在 Tool 内发布 Checkpoint，避免 Run 内回边。Checkpoint 必须不可变并至少绑定：`run_id/step_id/tool_id`、Tool 契约版本、`input_snapshot_hash`、已完成边界、恢复 token、状态文件相对路径与 sha256、创建者和时间；正式目标 Schema 见 [`schema/proposed/Checkpoint.schema.json`](./schema/proposed/Checkpoint.schema.json)。恢复前 Runner 必须逐项校验，任何不匹配都拒绝 resume、退回新的完整 Step Attempt，不能猜测兼容。

> **现状（团队审核）**：这**是目标、不是现行通用能力**。当前是**单步执行器**（见 A），**没有**一个通用调度循环去"自动从首个未完成步骤续跑"。口播链路的续跑是 router/后台任务按需触发的特定实现。"文件化状态即可续跑"成立，但"自动续跑"未通用化。

### E5. 业务返工用新 Run，不在图中造循环 `[proposed]`

补件、审批退回、数据修正、尽调追加材料都在同一 `session + task` 下创建新 Run；新 Run 可带 `supersedes_run_id` 与 `input_revision_hash`。这不是 Step Attempt：它代表一次新的业务执行。若 `input_revision_hash` 变化，就是返工/重审；若哈希不变，则可表达显式整 Run 重跑。v0.4 不允许用 `depends_on` 回边模拟循环。

### E6. 运行控制词汇 `[proposed]`

“暂停/断点/重跑”不能共用一个按钮或命令名。规范只定义控制语义，具体 UI 可自行投影：

| 控制 | 精确定义 | 是否中断当前进程 | 能力门控 |
|---|---|---:|---|
| Pause Before Step | 当前在飞步骤可结束；下一 Step 调度前暂停 | 否 | Runtime |
| Pause After Step | 目标 Step 完成后不再调度其后继；canonical Run 进入 `waiting(user)`，不是业务完成 | 否 | Runtime |
| Stop After Current | 当前在飞步骤结束后停止本 Run，不再启动新步骤 | 否 | Runtime |
| Terminate Current | 请求终止当前 Tool 进程/外部调用，结果为 cancelled/failed | 是 | `supports_safe_terminate` |
| Resume Run | 继续同一未终止 Run；恢复前重做全部依赖检查 | 否 | Runtime；有 checkpoint 时还需 `supports_resume` |
| Rerun | 创建新 Run，从选定边界或全链执行 | 新执行 | Runtime |
| Diagnostic Single Step | 隔离运行指定 Step；默认不发布正式 Manifest、不推进 canonical Run | 取决于 Tool | Runtime/权限 |

控制命令必须带 `command_id/idempotency_key/expected_revision/actor_id` 并写审计事件。UI 只能根据后端 capabilities 开关操作；不支持安全终止的 Tool 不能仅靠前端按钮声称“可取消”。

#### E6.1 四种用户临时执行意图

用户看到的是“全跑 / 跑到这里 / 从这里重跑 / 只重跑这里”，Runtime 收到的必须是可审计、可静态编译的执行范围，不能把 UI 上的卡片顺序当成依赖：

| 用户操作 | 目标执行范围 | canonical 结果语义 | OpenCrew 现状 |
|---|---|---|---|
| **运行整个流程** | `full`：从所有激活根节点执行到恰好一个 outcome；guard 关闭的分支仍为 `skipped`，不是“每张卡都调用” | 创建新 Run，只有 outcome 的终点与必需 Artifact 闭合后才 `completed` | `Analysis_V1 mode=run_all/rerun_all` 已按领域步骤表执行；尚非通用 Process DAG Runtime |
| **运行到某步骤** | `through_step`：目标 Step 的传递前置闭包 + 目标本身 | 若准备以后继续，目标完成后执行 `pause_after_target`，Run 进入 `waiting(user)`；若只为试算，则必须标为 diagnostic。**部分执行不能冒充业务 completed/outcome** | `run_range` 已能执行线性起止区间，但完成后把领域 attempt 标为 completed 并同步文件；这不等于 FlowSpec canonical Run 已完成 |
| **从某步骤开始重新运行** | `from_step`：强制重算所选 Step，并按 DAG 计算受影响后继闭包；汇合点所需的独立上游可验证复用 | 已终止 Run 不原地改写；创建新 Run，引用来源 Run。上游只有在 producer Attempt 派生摘要、Artifact schema/hash、按需 binding 与冻结定义兼容时才可 reused | `run_from_step/rerun_from_step` 已执行领域步骤表中的线性后缀；边界前步骤当前按位置无条件标 `reused`，`plan_hash_match` 只作信息展示 |
| **重新单独运行某步骤** | `only_step` 默认是隔离诊断：读取冻结输入快照，产生独立日志、Usage 与 diagnostic Artifact | 不发布 canonical Manifest、不改变既有业务 outcome。若输出要成为正式结果，必须改用 `from_step`，使受影响后继失效并重算；活动 Run 内对失败 Step 的再次调度则是新 Step Attempt，不是新 Run | `run_only_step` 已存在且记 `billing_scope=diagnostic`，但仍走领域 attempt/result manifest 与文件同步路径，尚未实现目标所说的发布隔离 |

`run_selected_steps`、任意起止区间等可以作为高级诊断能力，但不能绕过同一套依赖、权限、成本和发布规则。尤其在 DAG 中，“从 S 开始”是**图上的受影响后继闭包**，不是定义文件中 `S` 之后的所有行；“运行到 T”是 **T 的前置闭包**，不是按序号从第一行切到 T。

#### E6.2 范围编译必须守住的不变量

1. **依赖闭包**：编译器同时解析 `depends_on`、变量生产者与 Artifact producer；缺少必要前置时 fail closed，并返回具体 missing dependency，不能靠用户勾选强行跳过。
2. **fork / join 正确性**：一对多分叉可并行；多上游 AND-join 必须全部满足，`any_of` OR-join 至少一个满足。`from_step` 到达 join 时，其他输入要么验证复用，要么纳入重算范围。
3. **复用与失效**：用户选择边界只表达意图，不证明旧产物仍有效。复用 canonical Artifact 必须校验 producer Attempt 的声明输入摘要、定义/Tool/Profile、Artifact hash/schema，以及 contract 要求时的 binding；任一相关变化都使受影响下游 stale。不得因无关 session 字段变化而全量失效，也不得把 Working/日志/投影纳入复用候选。
4. **发布隔离**：diagnostic 输出与 canonical Artifact/Context 分区；只有 finalize 成功的 canonical Attempt 才能发布正式 Manifest。已 completed 的 Run 永不原地改写。
5. **副作用与费用**：重跑仍按 `side_effect_class` 决定重算、幂等、对账或拒绝；每个新 billable operation 重新经过授权、预算预留和 Usage 记账，不能把“单步”理解成“不计费”。
6. **控制并发**：命令用 CAS + 幂等键；Runtime 根据当前 Run/Step 状态和 Tool capability 返回可用操作。两个操作者同时改变边界时，一个成功、另一个拿 409 + 最新 snapshot，不可静默覆盖。

> **现行精度边界 `[implemented/domain-specific]`**：`backend/opcrew_backend/koubo/router.py` 的 `ANALYSIS_V1_RUN_MODES` 和前端 `AnalysisV1Module.jsx` 已提供上述四类入口，且会检查所选 Step、起止顺序与 workspace 输入是否存在。这是很有价值的生产来源，但它编译的是领域硬编码的线性步骤表，不消费 FlowSpec Process DAG；通用 scope compiler、hash-gated reuse、diagnostic publication isolation 与统一 Command/CAS 信封仍是 `[proposed]`。复用事实见 [09·L4.6](./09_ProductionLessons.md)。

## F. 事件与可观测性（Events & Observability）

### F1. 两种投递并存

- **轮询快照**：`GET .../tasks/{task_id}` 返回当前状态聚合（含每步状态、卡因、产物状态）；目标快照还带单调 `revision`、`event_cursor` 与 `generated_at`。前端定时拉取渲染进度。
- **事件流**：`GET .../events?since=<cursor>`（增量轮询）与 `.../events/stream`（SSE）推送状态变化。

参考实现：`session_events` 表（`step_id/tool_id/attempt_id/workflow_id`）；runner 发 `tool_session.step.{started,completed,failed,blocked,stale_running}`；快照 `video_plan_execution_payload`（`plan_hash/artifact_status/execution_state/binding_status`）；路由 `routes/sessions.py`（events + SSE）、`koubo_storyboard/task_routes.py`（详情）。
> **前端事实（[implemented]，已证实，非"未证实"）**：口播三个执行弹窗**用的是轮询快照，不是 SSE**——`KouboVideoPlanModal.jsx:396`/`KouboImagePlanModal.jsx:331`（每秒）、`KouboVideoOnlyPlanModal.jsx:236`（运行~1.6s / 空闲 5s），快照 API `kouboStoryboardApi.js:184`。平台 SSE 存在但这些弹窗未订阅；未来可切 SSE。

### F2. "卡在什么上"必须可查（Waiting Info，借 Control-M）

`waiting/blocked` 步骤的事件与快照必须带 `wait_reason`（kind + detail），让运维能一眼看出卡在哪个条件/资源/产物/人工确认——而不是一个不透明的"pending"。这是 Control-M 相对多数引擎最值得抄的可观测性设计。

### F3. 事件契约（Event Envelope）

```jsonc
{
  "schema_version":"1.1", "event_id": "...", "cursor": 10432,
  "kind": "step.blocked",                // 目标含 step.{started|completed|failed|blocked|skipped|stale} | run.* | plan.*
  "session_id": "loan_00123", "task_id": "loan_00123_t", "run_id": "run_...", "step_id": "S4_risk_score",
  "tool_id": "risk_score", "step_attempt_id":"sa_...", "step_attempt_no": 2,
  "correlation_id":"run_...",
  "actor": { "type":"system|user|service", "id":"...", "roles":[] },
  "payload": { "wait_reason": { "kind": "artifact", "detail": "CreditReport.json" } },
  "at": "2026-07-23T09:12:00Z"
}
```

`step_attempt_id` 是稳定关联键，`step_attempt_no` 只帮助人阅读；二者必须同时为空或同时有值。Run 级事件与尚未创建 Attempt 的 `step.skipped` 使用 `null/null`；Artifact 发布、模型调用、预算、Human Task、callback 及 Step 执行事件必须关联实际 Attempt。`wait_reason` 只放在 `payload`，避免顶层与 payload 双写后产生冲突。

AI 调用还应发 `model.invocation.{started|usage_updated|completed|ambiguous|failed}` 与 `budget.{reserved|released|exhausted}`。流式 `usage_updated` 是可变观测；最终 UsageRecord 才是去重后的计量事实，provider 发票对账可再追加 superseding 记录。

### F4. 多客户端一致性协议 `[proposed]`

Snapshot、事件和命令共同构成一致性契约，SSE/轮询只是可替换的传输：

1. 客户端先取 snapshot `{revision,event_cursor,...}`，再从 cursor 接收增量事件；发现游标缺口、重连或事件乱序时重新拉 snapshot。
2. SSE 用 cursor 续传；不可用时轮询 `events?since=<cursor>`，快照轮询可用 ETag/revision 短路。不能把“用了 SSE”误当成状态一致性。
3. 写命令带 `command_id`、稳定 idempotency key 与 `expected_revision`；服务端 CAS 冲突返回 409 和最新 snapshot/revision，不做静默覆盖。
4. 浏览器/localStorage 只保存视图偏好，不保存权威 Run/Step/Artifact 状态；多 Tab、多用户都以服务端 snapshot + event timeline 收敛。
5. 可人工编辑的内容用内容 sha256 做乐观并发；聚合状态 snapshot 使用单调 revision。`updated_at` 只用于显示和诊断，不能替代二者。

OpenCrew 已有 session event cursor/SSE 与专用轮询快照，但尚无覆盖所有 Run 控制命令的统一 revision/CAS 协议，因此本节整体为目标契约。

### F5. 日志平面：事件、诊断、计量不能混在一起

“运行记录”不是一种存储。FlowSpec 把它拆成下列事实平面；每一平面只有一个权威来源，UI 可以组合展示，但不能互相反推：

| 平面 | 内容 | 权威存储 | 不应放入 |
|---|---|---|---|
| 状态快照 | Run/Step 当前状态、revision、wait reason | 运行时数据库/一致性快照；现行部分 State 仍在文件 | 大段 stdout、Prompt 正文 |
| 审计事件 | 状态转换、人工决定、发布、预算门控、操作者与原因 | **数据库 append-only**；OpenCrew 当前为 `session_events` | 每行调试输出、模型账单明细 |
| Usage / Cost | billable operation、单位、估算/实际费用、预算流水 | **计量表/不可变 ledger**；当前为 `local_usage_log`，目标为 UsageRecord/BudgetLedger | 从文本日志正则反推 token 或费用 |
| Step Attempt 诊断日志 | runtime/tool adapter、结构化诊断、stdout/stderr、错误上下文 | **Workspace 文件或对象日志存储**；DB 只存 locator、摘要与 tail 索引 | 业务状态权威、唯一 Artifact |
| Prompt / Agent transcript | 实际 prompt、受控工具轨迹、模型调用审计引用 | Step 的 `Prompt/` 或受控外部存储 | 混进普通日志导致越权扩散 |
| 服务/进程日志 | API、worker、scheduler、DB、部署健康 | 平台日志 sink，位于 session Workspace 之外 | session Artifact 与客户可见审计记录 |

日志级别只适用于**诊断和告警优先级**，不能决定审计事实是否保存：

| level | 使用条件 | 生产默认行为 |
|---|---|---|
| `debug` | 细粒度调度判断、adapter 参数摘要、重试细节 | 默认关闭或短期采样；不得含密钥/完整输入正文 |
| `info` | Attempt 开始/结束、外部调用阶段、checkpoint、发布摘要 | 默认写入诊断文件；重要状态变化另发 durable Event |
| `warning` | 可恢复降级、接近预算/容量、截断、对账待定 | 保留并可触发运营告警；不自动把 Step 判失败 |
| `error` | 当前操作/Attempt 失败，需要恢复或人工处理 | 写结构化 Error + Event；关联 Attempt，不只留文本 |
| `critical` | 数据完整性、权限、安全或平台级故障，可能影响多个 Run | 立即告警；仍由状态机逐 Run/Step 判定业务后果 |

`stdout`/`stderr` 是 **channel**，不是 level：有些工具会把正常进度写到 stderr，Adapter 不得据此自动标成 `error`。审计 Event 的 `severity` 可使用 `info|warning|error|critical`，但 Event 不能因部署把日志阈值调高而丢失。

#### F5.1 Diagnostic Log Record `[proposed]`

结构化日志采用 UTF-8 NDJSON，一行一个记录；stdout/stderr 另存原始文本，并由同一 Attempt locator 关联：

```jsonc
{
  "schema_version":"1.0",
  "timestamp":"2026-07-24T05:21:14.102Z",
  "sequence":17,
  "level":"info",
  "channel":"tool",                    // runtime | adapter | tool | stdout | stderr | model_adapter
  "message":"mock credit bureau response validated",
  "session_id":"loan_00123", "task_id":"loan_00123_t",
  "run_id":"run_...", "step_id":"S2_credit_pull",
  "step_attempt_id":"sa_...", "step_attempt_no":1,
  "tool_id":"credit_bureau_pull",
  "correlation_id":"run_...",
  "visibility":"internal",             // public | internal | restricted
  "sensitivity":"normal",              // normal | sensitive | secret
  "fields":{"duration_ms":420,"mock":true}
}
```

必须有 Attempt 身份和单调 `sequence`，以便多进程时间戳相同时仍可排序。`message` 面向人，稳定查询字段放 `fields`；不允许解析 message 来恢复状态。凭证、Authorization/Cookie、未获授权的个人信息必须在**写盘前**脱敏；Prompt/Artifact 正文只留受控引用和 hash。单条大小、文件分段、总字节数和 retention class 必须由部署策略设上限，截断时写一条 `warning` 记录而不是静默丢弃。

正式 Schema：[`schema/proposed/DiagnosticLogRecord.schema.json`](./schema/proposed/DiagnosticLogRecord.schema.json)。

#### F5.2 日志文件位置与索引 `[proposed/additive]`

在不改变 OpenCrew 既有四目录的前提下：

```text
<run>/SessionReport/Runtime.ndjson
<run>/S{index}_{tool}/Report/Attempts/A{no}_{attempt_id}/diagnostic.ndjson
<run>/S{index}_{tool}/Report/Attempts/A{no}_{attempt_id}/stdout.log
<run>/S{index}_{tool}/Report/Attempts/A{no}_{attempt_id}/stderr.log
<run>/S{index}_{tool}/Report/Attempts/A{no}_{attempt_id}/error.json
```

- `Runtime.ndjson` 只收 Run 调度/adapter 诊断；不复制 `session_events` 全文。若需要统一时间线，生成可重建的索引/投影。
- 每个 Attempt 独占 Report 子目录；重跑不能覆盖前一 Attempt 日志。DB 可记录 `DiagnosticLogRef{base,path,first_sequence,last_sequence,bytes,sha256,retention_class}`，不把高容量原文塞进事件表。
- UI 的 live tail 是文件/日志服务的**有界投影**，需要明确 `truncated`、`next_cursor` 和 audience；不是权威日志副本。
- 本地文件路径只是一个 storage binding；云部署可把同一 locator 解析到对象存储/日志服务。FlowSpec 不规定 `/var/log` 或某个供应商。

#### F5.3 OpenCrew 现状映射精度

- `[implemented]` `session_events` 已落数据库，带 `visibility/event_scope/severity/family` 与 session/task/attempt/tool/step 关联；`SessionEventService` 在写入/展示路径做密钥、邮箱、电话等脱敏。字段是普通文本列，尚未由 Schema 封闭成上述枚举。
- `[implemented/legacy]` `task_logs(task_id,phase,level,message,created_at)` 服务旧 task-run 路径，不是所有 Tool Session 的统一日志平面。
- `[implemented/domain-specific]` Analysis_V1 已把 stdout/stderr 写到 `<workspace>/SessionReport/tool_runs/attempt_<attempt_id>/logs/<step_id>.{stdout|stderr}.log`，并向 UI 暴露脱敏 tail；这证明 Attempt 文件日志可行，但位置仍是领域绑定。
- `[implemented/partial]` 通用 Tool Session 会创建 Step `Report/`，子进程也 `capture_output=True`，但目前只把 stderr/stdout 摘要放进 `ToolResult`，**没有通用地把完整 stdout/stderr 写入 Report**。因此“Report 目录存在”不等于“日志已持久化”。
- `[implemented/local-dev binding]` `opencrew_local_stack.sh` 默认把 backend/frontend 进程输出写到 `/tmp/opencrew-{backend|frontend}.log`（可由 `OPENCREW_*_LOG` 改写），本地 PostgreSQL 默认 `~/.opencrew/postgres/postgres.log`（可由 `OPENCREW_PG_LOG_PATH` 改写）。这些是易失/部署路径且启动脚本会重建日志文件，**不是 FlowSpec 业务日志规范，也不是生产 retention 方案**。
- `[implemented]` `/api/session-tasks/{session_id}/logs` 当前只是 customer-visible `session_events` 的文本投影，不读取 Workspace 原始日志。命名为 logs 不代表它是 stdout/stderr 文件接口。

#### F5.4 保留、删除与访问控制

每个部署必须把 `retention_class` 解析为明确的保存期限、归档层、最大字节数和 legal hold 行为；核心规范不硬编码天数。最低约束：

1. Event、Usage、Artifact Manifest 按业务审计策略保留，不能随 debug 日志清理一起删除。
2. `Working/.staging` 最短；Step 诊断日志次之；Prompt/Agent transcript 按其数据分类单独授权，不能默认与普通 info 日志同级开放。
3. 删除 session/task 时，数据库事实、Workspace、外部 Artifact/日志 locator 与对象版本必须由同一删除工作流清点；部分失败要可重试、可对账。
4. legal hold、投诉/审计冻结优先于常规 TTL；清理动作本身发 durable Event。
5. 客户、开发、运维、审计四类 audience 分权；任何公开/分享接口都不能通过 `debug_ref` 或绝对路径绕过下载授权。

## G. SLA overlay 🆕

借 Control-M BIM：**业务截止是服务级承诺，与单步超时分离**（[00·P10]）。

- 只声明服务级截止：`complete_by`（绝对时刻）或 `complete_in`（相对时长）+ 优先级。
- **关键路径默认从 DAG + 历史耗时自动推导**（[00·P10]）；手工 `critical_path` 只能是**显式 override**，不是必填。
- 基于历史统计（平均耗时/标准差）做**预测性**告警——预计将超期就告警，而非事后。触发：告警 / 升级 / 建工单。

```jsonc
"sla": [ { "service": "贷款审批时效",
           "complete_in_seconds": 172800, "priority": 1,
           "critical_path_override": ["S1","S4","S6","S8"],   // 可选 override；默认从 DAG 推导
           "on_breach": [ {"do":"notify","target":"risk_manager"}, {"do":"escalate"} ] } ]
```

OpenCrew 当前无 SLA 层——纯建议补强。

## H. 检查清单

- [ ] "能不能跑"建模为统一谓词集（条件+变量+产物+资源+确认）？
- [ ] `waiting/blocked` 一律带可查 `wait_reason`？
- [ ] “仍可到达”与“已不可到达”是否分开处理，未激活分支能否递归 skipped；Run 是否恰好一个 outcome 收口？
- [ ] Confirm 是否先创建 durable work item 再 waiting，决定是否校验 role/reason/input hash/expected revision？
- [ ] 资源可用性检查与 claim/lease 获取是否原子，并有 TTL/fencing？
- [ ] 资源是 `mutex`/`semaphore`/`rate_limit` **三个**独立原语（速率≠并发）？昂贵步骤按资源限流？
- [ ] 每个 Model Invocation 是否有稳定 operation key、UsageRecord、token/媒体单位与费用状态？预算是否调用前门控、调用后按实际结算？
- [ ] Transport retry / Step Attempt / Checkpoint resume / Run rerun 是否分层，且 operation key 在技术重试间稳定？
- [ ] RetryPolicy 与 FailurePropagation 是否分开，独立分支由 DAG 证明？
- [ ] On-Do 是否只处理重试后的动作，不另开自动 rerun 入口？
- [ ] 有心跳 + stale/orphan 检测 + 经过版本/哈希/校验和验证的断点续跑？
- [ ] 运行控制是否 capability-gated，Resume 前是否重跑依赖自检？
- [ ] 事件流 + 带 revision/cursor 的快照是否都提供，命令是否 CAS + 幂等？关键路径有 SLA overlay？
- [ ] Event、Usage、Step 诊断、Prompt 与服务日志是否分平面存储，且各有唯一权威源？
- [ ] 诊断日志是否按 Run/Step Attempt 定位并保留 stdout/stderr channel，而非用 stderr 冒充 error level？
- [ ] 日志是否写盘前脱敏、按 audience 授权、具备分段/字节上限/retention/legal-hold 与可对账删除？
